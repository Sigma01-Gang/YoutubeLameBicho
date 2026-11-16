"""Persistencia SQLite: esquema, ajustes, historial y copias de seguridad."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ..config import DEFAULT_SETTINGS

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL DEFAULT 'es',
    folder TEXT NOT NULL DEFAULT '',
    profile_dir TEXT NOT NULL DEFAULT '',
    studio_lang TEXT NOT NULL DEFAULT 'es',
    sheet_path TEXT NOT NULL DEFAULT '',
    templates TEXT NOT NULL DEFAULT '{}',
    preferred_slots TEXT NOT NULL DEFAULT '[]',
    min_gap_minutes INTEGER NOT NULL DEFAULT 120,
    daily_limit INTEGER NOT NULL DEFAULT 6,
    rest_days TEXT NOT NULL DEFAULT '[]',
    paused INTEGER NOT NULL DEFAULT 0,
    playlist TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sheet_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL DEFAULT '',
    row_key TEXT NOT NULL,
    archivo_video TEXT NOT NULL DEFAULT '',
    titulo TEXT NOT NULL DEFAULT '',
    descripcion TEXT NOT NULL DEFAULT '',
    etiquetas TEXT NOT NULL DEFAULT '',
    fecha_hora_publicacion TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'pendiente',
    url_publicado TEXT NOT NULL DEFAULT '',
    grupo TEXT NOT NULL DEFAULT '',
    miniatura TEXT NOT NULL DEFAULT '',
    subtitulos TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '{}',
    errors TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    UNIQUE(channel_id, row_key)
);

CREATE TABLE IF NOT EXISTS row_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER NOT NULL REFERENCES sheet_rows(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);

CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER REFERENCES sheet_rows(id) ON DELETE SET NULL,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    video_path TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    publish_at TEXT NOT NULL DEFAULT '',
    scheduled_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    next_attempt_at TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    approved_sha TEXT NOT NULL DEFAULT '',
    upload_stage TEXT NOT NULL DEFAULT '',
    video_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL DEFAULT '',
    subtitles TEXT NOT NULL DEFAULT '',
    is_short INTEGER NOT NULL DEFAULT 1,
    group_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id INTEGER NOT NULL REFERENCES queue_items(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    approved_at TEXT NOT NULL,
    checklist TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS upload_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id INTEGER,
    channel_id INTEGER,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS render_cache (
    key TEXT PRIMARY KEY,
    output_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_items(status);
CREATE INDEX IF NOT EXISTS idx_queue_channel ON queue_items(channel_id);
CREATE INDEX IF NOT EXISTS idx_rows_channel ON sheet_rows(channel_id);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Database:
    """Envoltorio fino y seguro entre hilos sobre sqlite3."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))

    # --- primitivas -----------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- ajustes ----------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            if default is not None:
                return default
            return DEFAULT_SETTINGS.get(key)
        return json.loads(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def all_settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for row in self.query("SELECT key, value FROM settings"):
            out[row["key"]] = json.loads(row["value"])
        return out

    # --- registro ---------------------------------------------------------
    def log_upload(self, queue_item_id: int | None, channel_id: int | None, event: str, detail: str = "") -> None:
        self.execute(
            "INSERT INTO upload_log(queue_item_id, channel_id, at, event, detail) VALUES(?,?,?,?,?)",
            (queue_item_id, channel_id, now_iso(), event, detail),
        )

    def log_app(self, level: str, logger: str, message: str) -> None:
        self.execute(
            "INSERT INTO app_log(at, level, logger, message) VALUES(?,?,?,?)",
            (now_iso(), level, logger, message[:4000]),
        )

    def start_job(self, kind: str, label: str = "") -> int:
        cur = self.execute("INSERT INTO jobs(kind, label, started_at) VALUES(?,?,?)", (kind, label, now_iso()))
        return int(cur.lastrowid)

    def finish_job(self, job_id: int, ok: bool, seconds: float, error: str = "") -> None:
        self.execute(
            "UPDATE jobs SET finished_at=?, ok=?, seconds=?, error=? WHERE id=?",
            (now_iso(), int(ok), seconds, error[:2000], job_id),
        )

    # --- copias de seguridad ---------------------------------------------
    def backup(self, dest_dir: str | Path, keep: int = 10, extra_files: list[str] | None = None) -> Path:
        """Copia la base de datos (API de backup de SQLite) y planillas a una carpeta con marca de tiempo."""
        dest_dir = Path(dest_dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = dest_dir / f"backup_{stamp}"
        target.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dst = sqlite3.connect(str(target / "shortsfactory.db"))
            try:
                self._conn.backup(dst)
            finally:
                dst.close()
        for f in extra_files or []:
            p = Path(f)
            if p.is_file():
                shutil.copy2(p, target / p.name)
        backups = sorted([d for d in dest_dir.glob("backup_*") if d.is_dir()])
        for old in backups[:-keep] if keep > 0 else []:
            shutil.rmtree(old, ignore_errors=True)
        return target


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
