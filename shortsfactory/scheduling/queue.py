"""Cola persistente de subidas: estados, aprobación, reintentos y planificación."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..channels.service import ChannelService
from ..core.models import Status
from ..core.timeutil import parse_datetime
from ..library.files import sha256_file, verify_hash
from ..persistence.db import Database, now_iso
from .scheduler import ChannelRules, Entry, backoff_delay, parse_slot, plan_schedule

log = logging.getLogger(__name__)

ACTIVE = (Status.PENDING.value, Status.RETRYING.value, Status.AWAITING_APPROVAL.value)


def _dt(s: str) -> datetime | None:
    try:
        return parse_datetime(s) if s else None
    except ValueError:
        return None


class QueueService:
    def __init__(self, db: Database):
        self.db = db
        self.channels = ChannelService(db)

    # --- alta y consultas ------------------------------------------------------
    def add(self, channel_id: int, video_path: str, title: str = "", description: str = "", tags: str = "",
            publish_at: str = "", row_id: int | None = None, is_short: bool = True, thumbnail: str = "",
            subtitles: str = "", group_id: str = "") -> int:
        status = Status.AWAITING_APPROVAL if self.db.get_setting("approval_mode", True) else Status.PENDING
        pos = (self.db.one("SELECT COALESCE(MAX(position), 0) + 1 FROM queue_items")[0])
        cur = self.db.execute(
            "INSERT INTO queue_items(row_id, channel_id, video_path, title, description, tags, publish_at, status, "
            "position, is_short, thumbnail, subtitles, group_id, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row_id, channel_id, video_path, title, description, tags, publish_at, status.value, pos,
             int(is_short), thumbnail, subtitles, group_id, now_iso(), now_iso()),
        )
        item_id = int(cur.lastrowid)
        self.db.log_upload(item_id, channel_id, "encolado", Path(video_path).name)
        return item_id

    def add_from_row(self, row: dict, is_short: bool = True) -> int:
        existing = self.db.one("SELECT id FROM queue_items WHERE row_id=? AND status != 'publicado'", (row["id"],))
        if existing:
            return int(existing["id"])
        return self.add(row["channel_id"], row["archivo_video"], row["titulo"], row["descripcion"],
                        row["etiquetas"], row["fecha_hora_publicacion"], row["id"], is_short,
                        row.get("miniatura", ""), row.get("subtitulos", ""), row.get("grupo", ""))

    def get(self, item_id: int) -> dict | None:
        r = self.db.one("SELECT q.*, c.name AS canal FROM queue_items q JOIN channels c ON c.id=q.channel_id "
                        "WHERE q.id=?", (item_id,))
        return dict(r) if r else None

    def items(self, channel_id: int | None = None, status: str | None = None) -> list[dict]:
        sql = "SELECT q.*, c.name AS canal FROM queue_items q JOIN channels c ON c.id=q.channel_id WHERE 1=1"
        params: list = []
        if channel_id:
            sql += " AND q.channel_id=?"
            params.append(channel_id)
        if status:
            sql += " AND q.status=?"
            params.append(status)
        sql += " ORDER BY q.position, q.id"
        return [dict(r) for r in self.db.query(sql, tuple(params))]

    def update_meta(self, item_id: int, **fields) -> None:
        allowed = {"title", "description", "tags", "publish_at", "thumbnail", "subtitles", "video_path"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        if "video_path" in sets:  # un archivo nuevo es una nueva versión y requiere nueva aprobación
            self.db.execute("UPDATE queue_items SET version=version+1, approved_sha='' WHERE id=?", (item_id,))
            if self.db.get_setting("approval_mode", True):
                sets["status"] = Status.AWAITING_APPROVAL.value
        cols = ", ".join(f"{k}=?" for k in sets)
        self.db.execute(f"UPDATE queue_items SET {cols}, updated_at=? WHERE id=?",
                        (*sets.values(), now_iso(), item_id))

    def remove(self, item_id: int) -> None:
        self.db.execute("DELETE FROM queue_items WHERE id=?", (item_id,))

    # --- revisión humana ---------------------------------------------------
    def approve(self, item_id: int, checklist: dict | None = None, note: str = "") -> str:
        item = self.get(item_id)
        if item is None:
            raise ValueError("elemento no encontrado")
        sha = sha256_file(item["video_path"])
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO approvals(queue_item_id, sha256, version, approved_at, checklist, note) "
                         "VALUES(?,?,?,?,?,?)", (item_id, sha, item["version"], now_iso(),
                                                 json.dumps(checklist or {}, ensure_ascii=False), note))
            new_status = item["status"]
            if item["status"] in (Status.AWAITING_APPROVAL.value, Status.ERROR.value):
                new_status = Status.PENDING.value
            conn.execute("UPDATE queue_items SET approved_sha=?, status=?, updated_at=? WHERE id=?",
                         (sha, new_status, now_iso(), item_id))
        self.db.log_upload(item_id, item["channel_id"], "aprobado", f"v{item['version']} sha256={sha[:12]}")
        return sha

    def reject(self, item_id: int, note: str = "") -> None:
        item = self.get(item_id)
        self.db.execute("UPDATE queue_items SET status=?, approved_sha='', last_error=?, updated_at=? WHERE id=?",
                        (Status.AWAITING_APPROVAL.value, f"rechazado: {note}", now_iso(), item_id))
        if item:
            self.db.log_upload(item_id, item["channel_id"], "rechazado", note)

    def approvals(self, item_id: int) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM approvals WHERE queue_item_id=? ORDER BY id DESC",
                                               (item_id,))]

    def is_upload_allowed(self, item: dict) -> tuple[bool, str]:
        """Comprueba que el vídeo está aprobado y que el archivo es exactamente el aprobado (SHA-256)."""
        if not Path(item["video_path"]).is_file():
            return False, "el archivo de vídeo no existe"
        if self.db.get_setting("approval_mode", True):
            if not item["approved_sha"]:
                return False, "sin aprobación"
            if not verify_hash(item["video_path"], item["approved_sha"]):
                return False, "el archivo ha cambiado desde la aprobación (SHA-256 distinto)"
        return True, ""

    # --- recuperación ------------------------------------------------------------
    def recover_after_crash(self) -> int:
        cur = self.db.execute("UPDATE queue_items SET status=?, upload_stage=upload_stage, updated_at=? "
                              "WHERE status=?", (Status.PENDING.value, now_iso(), Status.UPLOADING.value))
        n = cur.rowcount or 0
        if n:
            log.warning("%d elemento(s) estaban «subiendo» tras un cierre inesperado; vuelven a pendientes", n)
        return n

    # --- planificación ----------------------------------------------------------
    def _rules(self) -> dict[int, ChannelRules]:
        rules = {}
        for c in self.channels.list():
            slots = []
            for s in c.preferred_slots:
                try:
                    slots.append(parse_slot(s))
                except ValueError:
                    log.warning("Franja horaria no válida en %s: %s", c.name, s)
            rules[c.id] = ChannelRules(c.id, slots, timedelta(minutes=c.min_gap_minutes), c.daily_limit,
                                       set(c.rest_days), c.paused)
        return rules

    def _history(self) -> list[tuple[int, datetime]]:
        out = []
        for r in self.db.query("SELECT channel_id, published_at FROM queue_items WHERE published_at != ''"):
            t = _dt(r["published_at"])
            if t:
                out.append((r["channel_id"], t))
        return out

    def interval(self) -> timedelta:
        return timedelta(hours=float(self.db.get_setting("upload_interval_hours", 3.0)))

    def last_upload_started(self) -> datetime | None:
        return _dt(self.db.get_setting("last_upload_started_at", "") or "")

    def plan(self, now: datetime | None = None, include_unapproved: bool = True) -> list[tuple[dict, datetime | None]]:
        """Simulación: qué se publicaría y cuándo (no sube nada)."""
        now = now or datetime.now()
        statuses = ACTIVE if include_unapproved else (Status.PENDING.value, Status.RETRYING.value)
        items = [i for i in self.items() if i["status"] in statuses]
        entries = [Entry(i["id"], i["channel_id"], _dt(i["next_attempt_at"])) for i in items]
        plan = plan_schedule(entries, self._rules(), self._history(), self.interval(), now,
                             self.last_upload_started(), bool(self.db.get_setting("global_pause", False)))
        by_id = {i["id"]: i for i in items}
        return [(by_id[i], t) for i, t in plan]

    def save_plan(self, now: datetime | None = None) -> None:
        for item, t in self.plan(now):
            self.db.execute("UPDATE queue_items SET scheduled_at=? WHERE id=?",
                            (t.isoformat(sep=" ", timespec="minutes") if t else "", item["id"]))

    def next_due(self, now: datetime | None = None) -> dict | None:
        """Siguiente elemento listo para subir ahora, o None. Respeta el intervalo aunque la app se reinicie."""
        now = now or datetime.now()
        if self.db.get_setting("global_pause", False):
            return None
        for item, t in self.plan(now, include_unapproved=False):
            if t is None:
                continue
            if t <= now:
                ok, _ = self.is_upload_allowed(item)
                if ok:
                    return item
            break  # respetar el orden: si el primero no toca aún, no se adelanta otro
        return None

    # --- transiciones ---------------------------------------------------------
    def mark_uploading(self, item_id: int) -> None:
        self.db.execute("UPDATE queue_items SET status=?, updated_at=? WHERE id=?",
                        (Status.UPLOADING.value, now_iso(), item_id))
        self.db.set_setting("last_upload_started_at", now_iso())
        item = self.get(item_id)
        self.db.log_upload(item_id, item["channel_id"] if item else None, "subiendo")

    def set_stage(self, item_id: int, stage: str) -> None:
        self.db.execute("UPDATE queue_items SET upload_stage=?, updated_at=? WHERE id=?", (stage, now_iso(), item_id))

    def mark_published(self, item_id: int, video_id: str, url: str) -> None:
        item = self.get(item_id)
        self.db.execute("UPDATE queue_items SET status=?, video_id=?, url=?, published_at=?, last_error='', "
                        "upload_stage='hecho', updated_at=? WHERE id=?",
                        (Status.PUBLISHED.value, video_id, url, now_iso(), now_iso(), item_id))
        if item and item["row_id"]:
            from ..sheets.sync import SheetService
            svc = SheetService(self.db)
            svc.update_field(item["row_id"], "estado", Status.PUBLISHED.value)
            svc.update_field(item["row_id"], "url_publicado", url)
        self.db.log_upload(item_id, item["channel_id"] if item else None, "publicado", url)

    def mark_failed(self, item_id: int, error: str, now: datetime | None = None, retryable: bool = True) -> str:
        now = now or datetime.now()
        item = self.get(item_id)
        if item is None:
            return Status.ERROR.value
        attempts = item["attempts"] + 1
        max_r = int(self.db.get_setting("max_retries", 3))
        if retryable and attempts < max_r:
            status = Status.RETRYING.value
            nxt = now + backoff_delay(attempts, float(self.db.get_setting("retry_base_minutes", 5)))
            nxt_s = nxt.isoformat(sep=" ", timespec="seconds")
        else:
            status, nxt_s = Status.ERROR.value, ""
        self.db.execute("UPDATE queue_items SET status=?, attempts=?, last_error=?, next_attempt_at=?, updated_at=? "
                        "WHERE id=?", (status, attempts, error[:2000], nxt_s, now_iso(), item_id))
        if item["row_id"]:
            from ..sheets.sync import SheetService
            SheetService(self.db).update_field(item["row_id"], "estado", status)
        self.db.log_upload(item_id, item["channel_id"], status, f"intento {attempts}: {error[:500]}")
        return status

    def reset(self, item_id: int) -> None:
        self.db.execute("UPDATE queue_items SET status=?, attempts=0, next_attempt_at='', last_error='', "
                        "updated_at=? WHERE id=?", (Status.PENDING.value, now_iso(), item_id))

    # --- reordenar y desplazar -----------------------------------------------
    def reorder(self, ordered_ids: list[int]) -> None:
        with self.db.transaction() as conn:
            for pos, iid in enumerate(ordered_ids, 1):
                conn.execute("UPDATE queue_items SET position=? WHERE id=?", (pos, iid))

    def shift_dates(self, hours: float, channel_id: int | None = None) -> int:
        """Desplaza en bloque las fechas de publicación de los elementos no publicados."""
        n = 0
        for it in self.items(channel_id):
            if it["status"] == Status.PUBLISHED.value:
                continue
            t = _dt(it["publish_at"])
            if t:
                new = (t + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
                self.db.execute("UPDATE queue_items SET publish_at=? WHERE id=?", (new, it["id"]))
                if it["row_id"]:
                    from ..sheets.sync import SheetService
                    SheetService(self.db).update_field(it["row_id"], "fecha_hora_publicacion", new)
                n += 1
        return n
