"""Ejecutor de la cola: toma el siguiente elemento listo y lo sube con el subidor del canal."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from ..channels.service import Channel, ChannelService
from ..config import sub_dir
from ..core.timeutil import parse_datetime
from ..persistence.db import Database
from ..scheduling.queue import QueueService
from ..sheets.sheets import split_tags
from .studio import SessionExpired, StudioUploader, UploadError, UploadRequest, UploadResult

log = logging.getLogger(__name__)


class Uploader(Protocol):
    def upload(self, req: UploadRequest, on_stage: Callable[[str, dict], None] | None = None) -> UploadResult: ...


@dataclass
class TickResult:
    item_id: int | None
    outcome: str  # nada | publicado | reintentando | error | sesion_caducada | bloqueado
    message: str = ""


def default_uploader_factory(db: Database) -> Callable[[Channel], Uploader]:
    def make(ch: Channel) -> Uploader:
        return StudioUploader(ch.profile_dir, ch.studio_lang, str(sub_dir("diagnostico") / str(ch.id)))
    return make


class QueueRunner:
    def __init__(self, db: Database, uploader_factory: Callable[[Channel], Uploader] | None = None,
                 notify: Callable[[str, str], None] | None = None):
        self.db = db
        self.queue = QueueService(db)
        self.channels = ChannelService(db)
        self.factory = uploader_factory or default_uploader_factory(db)
        self.notify = notify or (lambda title, msg: None)

    def build_request(self, item: dict) -> UploadRequest:
        publish_at = None
        try:
            publish_at = parse_datetime(item["publish_at"]) if item["publish_at"] else None
        except ValueError:
            publish_at = None
        visibility = "PUBLIC"
        if self.db.get_setting("test_mode", False):
            visibility = self.db.get_setting("test_visibility", "PRIVATE")
            publish_at = None
        ch = self.channels.get(item["channel_id"])
        return UploadRequest(
            video_path=item["video_path"], title=item["title"], description=item["description"],
            tags=split_tags(item["tags"]), publish_at=publish_at, visibility=visibility,
            thumbnail=item["thumbnail"], playlist=ch.playlist if ch else "", subtitles=item["subtitles"],
            existing_video_id=item["video_id"] if item["upload_stage"] in ("archivo", "detalles", "visibilidad") else "",
        )

    def tick(self, now: datetime | None = None, simulate: bool = False) -> TickResult:
        """Una iteración de la cola. En simulación no sube nada."""
        if simulate or self.db.get_setting("simulation_mode", False):
            plan = self.queue.plan(now)
            nxt = next(((i, t) for i, t in plan if t), None)
            msg = f"Simulación: siguiente «{nxt[0]['title']}» a las {nxt[1]:%Y-%m-%d %H:%M}" if nxt else "Simulación: nada"
            return TickResult(nxt[0]["id"] if nxt else None, "nada", msg)
        item = self.queue.next_due(now)
        if item is None:
            return TickResult(None, "nada")
        allowed, why = self.queue.is_upload_allowed(item)
        if not allowed:  # doble comprobación justo antes de subir
            return TickResult(item["id"], "bloqueado", why)
        ch = self.channels.get(item["channel_id"])
        if ch is None or ch.paused:
            return TickResult(item["id"], "bloqueado", "canal en pausa")
        self.queue.mark_uploading(item["id"])
        uploader = self.factory(ch)

        def on_stage(stage: str, data: dict) -> None:
            self.queue.set_stage(item["id"], stage)
            if data.get("video_id"):
                self.db.execute("UPDATE queue_items SET video_id=? WHERE id=?", (data["video_id"], item["id"]))
            self.db.log_upload(item["id"], ch.id, f"etapa:{stage}", str(data))

        try:
            res = uploader.upload(self.build_request(item), on_stage)
        except SessionExpired as exc:
            self.channels.set_paused(ch.id, True)
            self.db.execute("UPDATE queue_items SET status='pendiente', last_error=? WHERE id=?", (str(exc), item["id"]))
            self.db.log_upload(item["id"], ch.id, "sesion_caducada", str(exc))
            self.notify("Sesión caducada", f"Canal «{ch.name}» en pausa: inicia sesión en YouTube Studio a mano.")
            return TickResult(item["id"], "sesion_caducada", str(exc))
        except UploadError as exc:
            detail = f"{exc}" + (f" (diagnóstico: {exc.diagnostic})" if exc.diagnostic else "")
            status = self.queue.mark_failed(item["id"], detail, now, exc.retryable)
            self.notify("Error de subida", f"{Path(item['video_path']).name}: {exc}")
            return TickResult(item["id"], status, detail)
        except Exception as exc:  # noqa: BLE001
            status = self.queue.mark_failed(item["id"], f"{type(exc).__name__}: {exc}", now)
            self.notify("Error de subida", f"{Path(item['video_path']).name}: {exc}")
            return TickResult(item["id"], status, str(exc))
        self.queue.mark_published(item["id"], res.video_id, res.url)
        for w in res.warnings:
            self.db.log_upload(item["id"], ch.id, "aviso", w)
        remaining = [i for i, t in self.queue.plan(now) if t]
        if not remaining:
            self.notify("Cola terminada", "No quedan vídeos pendientes de subir.")
        return TickResult(item["id"], "publicado", res.url)
