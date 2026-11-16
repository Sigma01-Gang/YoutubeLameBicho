"""Sincronización de planillas con la base de datos (sin perder estados ni historial)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..channels.service import ChannelService
from ..core.models import Status
from ..persistence.db import Database, now_iso
from . import sheets

SYNC_FIELDS = ["archivo_video", "titulo", "descripcion", "etiquetas", "fecha_hora_publicacion", "grupo",
               "miniatura", "subtitulos"]
# Campos gestionados por la app: el valor de la base de datos prevalece si ya existe.
APP_FIELDS = ["estado", "url_publicado"]


@dataclass
class SyncReport:
    source: str
    channel: str
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    results: list[sheets.RowResult] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def row_key(row: dict) -> str:
    return Path(row.get("archivo_video", "")).name.lower() or f"fila-{row.get('_index', '')}"


class SheetService:
    def __init__(self, db: Database):
        self.db = db
        self.channels = ChannelService(db)

    def published_titles(self, exclude_row_id: int | None = None) -> set[str]:
        rows = self.db.query(
            "SELECT title FROM queue_items WHERE status='publicado' UNION "
            "SELECT titulo FROM sheet_rows WHERE estado='publicado'")
        return {r[0] for r in rows if r[0]}

    def load_source(self, path: str, channel_name: str | None = None, check_files: bool = True) -> SyncReport:
        """Carga una planilla como fuente independiente. Si no se indica canal, se usa la columna «canal»."""
        rows = sheets.read_table(path)
        report = SyncReport(source=str(path), channel=channel_name or "")
        report.missing_columns = sheets.missing_columns(rows)
        if not rows:
            return report
        default_channel = channel_name or (rows[0].get("canal") or "")
        ch = self.channels.by_name(default_channel) if default_channel else None
        if ch is not None:
            rows = sheets.apply_rules(rows, ch.templates, self.db.get_setting("blocks", {}))
        base_dirs = [Path(path).parent] + ([ch.folder] if ch and ch.folder else [])
        report.results = sheets.validate_rows(rows, base_dirs, check_files, self.published_titles())
        for res in report.results:
            name = channel_name or res.data.get("canal") or default_channel
            chan = self.channels.by_name(name) if name else None
            if chan is None:
                res.errors.append(f"canal desconocido: «{name}»")
                continue
            report.channel = chan.name
            if chan.sheet_path != str(path):
                chan.sheet_path = str(path)
                self.channels.update(chan)
            # resolver ruta relativa
            resolved = sheets.resolve_video_path(res.data.get("archivo_video", ""), base_dirs + [chan.folder])
            if resolved:
                res.data["archivo_video"] = str(resolved)
            outcome = self._upsert(chan.id, str(path), res)
            setattr(report, outcome, getattr(report, outcome) + 1)
        return report

    def _upsert(self, channel_id: int, source: str, res: sheets.RowResult) -> str:
        d = res.data
        key = row_key(d)
        existing = self.db.one("SELECT * FROM sheet_rows WHERE channel_id=? AND row_key=?", (channel_id, key))
        errors = json.dumps(res.errors + [f"aviso: {w}" for w in res.warnings], ensure_ascii=False)
        extra = json.dumps({k: v for k, v in d.items() if k not in sheets.ALL_COLUMNS}, ensure_ascii=False)
        if existing is None:
            estado = d.get("estado", "").strip().lower()
            estado = estado if estado in Status.values() else Status.PENDING.value
            self.db.execute(
                "INSERT INTO sheet_rows(channel_id, source_path, row_key, archivo_video, titulo, descripcion, "
                "etiquetas, fecha_hora_publicacion, estado, url_publicado, grupo, miniatura, subtitulos, extra, "
                "errors, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (channel_id, source, key, d.get("archivo_video", ""), d.get("titulo", ""), d.get("descripcion", ""),
                 d.get("etiquetas", ""), d.get("fecha_hora_publicacion", ""), estado, d.get("url_publicado", ""),
                 d.get("grupo", ""), d.get("miniatura", ""), d.get("subtitulos", ""), extra, errors, now_iso()),
            )
            return "inserted"
        changed = False
        with self.db.transaction() as conn:
            for f in SYNC_FIELDS:
                new = d.get(f, "")
                if (existing[f] or "") != new:
                    conn.execute("INSERT INTO row_history(row_id, changed_at, field, old_value, new_value) "
                                 "VALUES(?,?,?,?,?)", (existing["id"], now_iso(), f, existing[f], new))
                    conn.execute(f"UPDATE sheet_rows SET {f}=? WHERE id=?", (new, existing["id"]))
                    changed = True
            for f in APP_FIELDS:  # sólo se toma de la planilla si la base de datos no tiene valor
                new = d.get(f, "").strip()
                if new and not (existing[f] or "").strip() and new != existing[f]:
                    conn.execute("INSERT INTO row_history(row_id, changed_at, field, old_value, new_value) "
                                 "VALUES(?,?,?,?,?)", (existing["id"], now_iso(), f, existing[f], new))
                    conn.execute(f"UPDATE sheet_rows SET {f}=? WHERE id=?", (new, existing["id"]))
                    changed = True
            conn.execute("UPDATE sheet_rows SET errors=?, extra=?, source_path=?, updated_at=? WHERE id=?",
                         (errors, extra, source, now_iso(), existing["id"]))
        return "updated" if changed else "unchanged"

    # --- consultas ------------------------------------------------------------
    def rows(self, channel_id: int | None = None, estado: str | None = None, order: str = "fecha") -> list[dict]:
        sql = "SELECT r.*, c.name AS canal FROM sheet_rows r JOIN channels c ON c.id = r.channel_id WHERE 1=1"
        params: list = []
        if channel_id:
            sql += " AND r.channel_id=?"
            params.append(channel_id)
        if estado:
            sql += " AND r.estado=?"
            params.append(estado)
        sql += {"fecha": " ORDER BY r.fecha_hora_publicacion, r.id", "estado": " ORDER BY r.estado, r.id",
                "canal": " ORDER BY c.name, r.id"}.get(order, " ORDER BY r.id")
        return [dict(r) for r in self.db.query(sql, tuple(params))]

    def history(self, row_id: int) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM row_history WHERE row_id=? ORDER BY id DESC", (row_id,))]

    def update_field(self, row_id: int, field_name: str, value: str) -> None:
        if field_name not in SYNC_FIELDS + APP_FIELDS:
            raise ValueError(f"campo no editable: {field_name}")
        old = self.db.one(f"SELECT {field_name} FROM sheet_rows WHERE id=?", (row_id,))
        if old is None or old[0] == value:
            return
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO row_history(row_id, changed_at, field, old_value, new_value) VALUES(?,?,?,?,?)",
                         (row_id, now_iso(), field_name, old[0], value))
            conn.execute(f"UPDATE sheet_rows SET {field_name}=?, updated_at=? WHERE id=?", (value, now_iso(), row_id))

    def export(self, path: str, channel_id: int | None = None) -> Path:
        out = []
        for r in self.rows(channel_id):
            row = {c: r.get(c, "") for c in sheets.ALL_COLUMNS}
            row["canal"] = r["canal"]
            out.append(row)
        return sheets.write_table(out, path)

    def duplicate_titles(self) -> list[tuple[str, int]]:
        rows = self.db.query(
            "SELECT lower(trim(titulo)) t, COUNT(*) n FROM sheet_rows WHERE trim(titulo) != '' "
            "GROUP BY channel_id, lower(trim(titulo)) HAVING n > 1")
        return [(r["t"], r["n"]) for r in rows]
