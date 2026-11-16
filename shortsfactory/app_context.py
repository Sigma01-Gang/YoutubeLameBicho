"""Contenedor de servicios: punto único de acceso para la interfaz y los scripts."""
from __future__ import annotations

import csv
import json
import logging
import logging.handlers
from datetime import date, datetime, timedelta
from pathlib import Path

from .channels.service import ChannelService
from .config import data_dir, resolve_tool, sub_dir
from .ffmpeg import runner
from .library.files import AssetLibrary, cleanup_temp
from .persistence.db import Database
from .processing import Processor
from .scheduling.queue import QueueService
from .sheets.sync import SheetService


class DbLogHandler(logging.Handler):
    def __init__(self, db: Database):
        super().__init__(level=logging.INFO)
        self.db = db

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.db.log_app(record.levelname, record.name, self.format(record))
        except Exception:  # noqa: BLE001
            pass


def setup_logging(db: Database | None = None) -> Path:
    log_file = sub_dir("logs") / "shortsfactory.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if db is not None and not any(isinstance(h, DbLogHandler) for h in root.handlers):
        dh = DbLogHandler(db)
        dh.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(dh)
    return log_file


class AppContext:
    def __init__(self, db_path: str | Path | None = None):
        self.db = Database(db_path or data_dir() / "shortsfactory.db")
        self.channels = ChannelService(self.db)
        self.sheets = SheetService(self.db)
        self.queue = QueueService(self.db)
        self.assets = AssetLibrary(self.db)
        self.recovered = self.queue.recover_after_crash()
        cleanup_temp(sub_dir("tmp"))

    @property
    def tools(self) -> runner.Tools:
        return runner.Tools(resolve_tool("ffmpeg", self.db.get_setting("ffmpeg_path", "ffmpeg")),
                            resolve_tool("ffprobe", self.db.get_setting("ffprobe_path", "ffprobe")))

    @property
    def processor(self) -> Processor:
        return Processor(self.db, self.tools)

    def backup(self) -> Path:
        sheets = [c.sheet_path for c in self.channels.list() if c.sheet_path]
        return self.db.backup(sub_dir("backups"), int(self.db.get_setting("backup_keep", 10)), sheets)

    # --- estadísticas -----------------------------------------------------------
    def stats(self, days: int = 14) -> dict:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        per_day = [dict(r) for r in self.db.query(
            "SELECT substr(published_at,1,10) dia, COUNT(*) n FROM queue_items "
            "WHERE status='publicado' AND published_at >= ? GROUP BY dia ORDER BY dia", (since,))]
        tot = self.db.one("SELECT COUNT(*) n, SUM(event IN ('error','reintentando')) e FROM upload_log "
                          "WHERE event IN ('publicado','error','reintentando')")
        attempts = tot["n"] or 0
        errors = tot["e"] or 0
        jobs = self.db.one("SELECT AVG(seconds) s, COUNT(*) n, SUM(ok=0) f FROM jobs WHERE kind='render' "
                           "AND finished_at != ''")
        return {
            "per_day": per_day,
            "error_rate": (errors / attempts) if attempts else 0.0,
            "avg_render_seconds": jobs["s"] or 0.0,
            "renders": jobs["n"] or 0,
            "render_failures": jobs["f"] or 0,
            "channels": self.channels.stats(),
        }

    # --- exportaciones ------------------------------------------------------------
    def export_log(self, path: str | Path, day: date | None = None) -> Path:
        p = Path(path)
        sql = "SELECT at, level, logger, message FROM app_log"
        params: tuple = ()
        if day:
            sql += " WHERE substr(at,1,10)=?"
            params = (day.isoformat(),)
        rows = self.db.query(sql + " ORDER BY id", params)
        up = self.db.query("SELECT at, 'UPLOAD' level, event logger, detail message FROM upload_log" +
                           (" WHERE substr(at,1,10)=?" if day else "") + " ORDER BY id", params)
        allrows = sorted([tuple(r) for r in rows] + [tuple(r) for r in up])
        if p.suffix.lower() == ".csv":
            with open(p, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["fecha", "nivel", "origen", "mensaje"])
                w.writerows(allrows)
        else:
            p.write_text("\n".join(f"{a} [{b}] {c}: {d}" for a, b, c, d in allrows), encoding="utf-8")
        return p

    def daily_summary(self, path: str | Path, day: date | None = None) -> Path:
        day = day or date.today()
        d = day.isoformat()
        pub = self.db.query("SELECT q.title, q.url, c.name FROM queue_items q JOIN channels c ON c.id=q.channel_id "
                            "WHERE substr(q.published_at,1,10)=?", (d,))
        errs = self.db.query("SELECT at, detail FROM upload_log WHERE event='error' AND substr(at,1,10)=?", (d,))
        jobs = self.db.one("SELECT COUNT(*) n, AVG(seconds) s FROM jobs WHERE substr(started_at,1,10)=?", (d,))
        p = Path(path)
        if p.suffix.lower() == ".csv":
            with open(p, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["tipo", "canal", "titulo", "detalle"])
                for r in pub:
                    w.writerow(["publicado", r["name"], r["title"], r["url"]])
                for r in errs:
                    w.writerow(["error", "", "", f"{r['at']} {r['detail']}"])
        else:
            lines = [f"Resumen del {d}", f"Publicados: {len(pub)}"]
            lines += [f"  - [{r['name']}] {r['title']} {r['url']}" for r in pub]
            lines += [f"Errores: {len(errs)}"] + [f"  - {r['at']} {r['detail']}" for r in errs]
            lines += [f"Renders: {jobs['n'] or 0} (media {jobs['s'] or 0:.1f}s)"]
            p.write_text("\n".join(lines), encoding="utf-8")
        return p

    # --- proyectos -----------------------------------------------------------------
    def export_project(self, path: str | Path, project: dict | None = None) -> Path:
        data = {
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "settings": self.db.all_settings(),
            "channels": [{k: v for k, v in c.__dict__.items() if k not in ("id", "created_at")}
                         for c in self.channels.list()],
            "project": project or {},
        }
        p = Path(path)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return p

    def import_project(self, path: str | Path) -> dict:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, v in (data.get("settings") or {}).items():
            if k not in ("first_run_done", "last_upload_started_at"):
                self.db.set_setting(k, v)
        for c in data.get("channels") or []:
            existing = self.channels.by_name(c["name"])
            if existing is None:
                existing = self.channels.create(c["name"], c.get("language", "es"))
            for k, v in c.items():
                if k not in ("profile_dir",) and hasattr(existing, k):
                    setattr(existing, k, v)
            self.channels.update(existing)
        return data.get("project") or {}
