"""Perfiles de canal aislados (carpeta, sesión de navegador, plantillas y planilla propias)."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import sub_dir
from ..persistence.db import Database, now_iso

DEFAULT_TEMPLATES_ES = {
    "titulo": "{titulo} (Parte {parte})",
    "descripcion": "{titulo} — Parte {parte}.\n\n{bloque:suscripcion}\n{bloque:enlaces}\n{bloque:creditos}\n#historias #shorts",
    "etiquetas": "historias, {titulo}, parte {parte}",
}
DEFAULT_TEMPLATES_EN = {
    "titulo": "{titulo} (Part {parte})",
    "descripcion": "{titulo} — Part {parte}.\n\n{bloque:subscribe}\n{bloque:links}\n{bloque:credits}\n#stories #shorts",
    "etiquetas": "stories, {titulo}, part {parte}",
}


@dataclass
class Channel:
    id: int
    name: str
    language: str = "es"
    folder: str = ""
    profile_dir: str = ""
    studio_lang: str = "es"
    sheet_path: str = ""
    templates: dict = field(default_factory=dict)
    preferred_slots: list[str] = field(default_factory=list)  # ["09:00-12:00", "18:00-23:00"]
    min_gap_minutes: int = 120
    daily_limit: int = 6
    rest_days: list[int] = field(default_factory=list)  # 0=lunes … 6=domingo
    paused: bool = False
    playlist: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row) -> "Channel":
        d = dict(row)
        return cls(
            id=d["id"], name=d["name"], language=d["language"], folder=d["folder"], profile_dir=d["profile_dir"],
            studio_lang=d["studio_lang"], sheet_path=d["sheet_path"], templates=json.loads(d["templates"] or "{}"),
            preferred_slots=json.loads(d["preferred_slots"] or "[]"), min_gap_minutes=d["min_gap_minutes"],
            daily_limit=d["daily_limit"], rest_days=json.loads(d["rest_days"] or "[]"), paused=bool(d["paused"]),
            playlist=d["playlist"], created_at=d["created_at"],
        )


def _slug(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name.strip().lower()) or "canal"


class ChannelService:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[Channel]:
        return [Channel.from_row(r) for r in self.db.query("SELECT * FROM channels ORDER BY id")]

    def get(self, channel_id: int) -> Channel | None:
        r = self.db.one("SELECT * FROM channels WHERE id=?", (channel_id,))
        return Channel.from_row(r) if r else None

    def by_name(self, name: str) -> Channel | None:
        r = self.db.one("SELECT * FROM channels WHERE lower(name)=lower(?)", (name.strip(),))
        return Channel.from_row(r) if r else None

    def create(self, name: str, language: str = "es", folder: str = "", **kw) -> Channel:
        slug = _slug(name)
        profile = str(sub_dir("profiles") / slug)
        Path(profile).mkdir(parents=True, exist_ok=True)
        if not folder:
            folder = str(sub_dir("channels") / slug)
        Path(folder).mkdir(parents=True, exist_ok=True)
        templates = kw.pop("templates", None) or (DEFAULT_TEMPLATES_EN if language == "en" else DEFAULT_TEMPLATES_ES)
        cur = self.db.execute(
            "INSERT INTO channels(name, language, folder, profile_dir, studio_lang, sheet_path, templates, "
            "preferred_slots, min_gap_minutes, daily_limit, rest_days, playlist, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, language, folder, profile, kw.get("studio_lang", language), kw.get("sheet_path", ""),
             json.dumps(templates, ensure_ascii=False), json.dumps(kw.get("preferred_slots", [])),
             kw.get("min_gap_minutes", 120), kw.get("daily_limit", 6), json.dumps(kw.get("rest_days", [])),
             kw.get("playlist", ""), now_iso()),
        )
        return self.get(int(cur.lastrowid))  # type: ignore[return-value]

    def update(self, ch: Channel) -> None:
        self.db.execute(
            "UPDATE channels SET name=?, language=?, folder=?, profile_dir=?, studio_lang=?, sheet_path=?, "
            "templates=?, preferred_slots=?, min_gap_minutes=?, daily_limit=?, rest_days=?, paused=?, playlist=? "
            "WHERE id=?",
            (ch.name, ch.language, ch.folder, ch.profile_dir, ch.studio_lang, ch.sheet_path,
             json.dumps(ch.templates, ensure_ascii=False), json.dumps(ch.preferred_slots), ch.min_gap_minutes,
             ch.daily_limit, json.dumps(ch.rest_days), int(ch.paused), ch.playlist, ch.id),
        )

    def set_paused(self, channel_id: int, paused: bool) -> None:
        self.db.execute("UPDATE channels SET paused=? WHERE id=?", (int(paused), channel_id))

    def clone(self, channel_id: int, new_name: str, language: str) -> Channel:
        """Clona ajustes de un canal (no la sesión del navegador) para crear el de otro idioma."""
        src = self.get(channel_id)
        if src is None:
            raise ValueError("canal no encontrado")
        templates = dict(src.templates)
        if language == "en" and src.language != "en":
            templates = dict(DEFAULT_TEMPLATES_EN)
        return self.create(
            new_name, language, studio_lang=language, templates=templates,
            preferred_slots=src.preferred_slots, min_gap_minutes=src.min_gap_minutes,
            daily_limit=src.daily_limit, rest_days=src.rest_days, playlist="",
        )

    def delete(self, channel_id: int, remove_profile: bool = False) -> None:
        ch = self.get(channel_id)
        self.db.execute("DELETE FROM channels WHERE id=?", (channel_id,))
        if ch and remove_profile and ch.profile_dir:
            shutil.rmtree(ch.profile_dir, ignore_errors=True)

    # --- multi-idioma -------------------------------------------------------
    def translation_alerts(self) -> list[str]:
        """Avisos de vídeos en español sin versión en inglés (por grupo) o versiones sin título traducido."""
        alerts = []
        chans = {c.id: c for c in self.list()}
        en_ids = [c.id for c in chans.values() if c.language == "en"]
        es_ids = [c.id for c in chans.values() if c.language == "es"]
        if not en_ids or not es_ids:
            return alerts
        rows = [dict(r) for r in self.db.query("SELECT * FROM sheet_rows")]
        en_by_group = {r["grupo"]: r for r in rows if r["channel_id"] in en_ids and r["grupo"]}
        for r in rows:
            if r["channel_id"] in es_ids and r["grupo"]:
                twin = en_by_group.get(r["grupo"])
                if twin is None:
                    alerts.append(f"El grupo «{r['grupo']}» ({r['archivo_video']}) no tiene fila en el canal en inglés")
                elif not twin["titulo"].strip():
                    alerts.append(f"El grupo «{r['grupo']}» no tiene título traducido en inglés")
                elif twin["titulo"].strip().lower() == r["titulo"].strip().lower():
                    alerts.append(f"El grupo «{r['grupo']}» tiene el mismo título en ambos idiomas (¿sin traducir?)")
            if r["channel_id"] in en_ids and not r["titulo"].strip():
                alerts.append(f"La fila en inglés {r['archivo_video']} no tiene título")
        return alerts

    def stats(self) -> list[dict]:
        out = []
        for c in self.list():
            row = self.db.one(
                "SELECT COUNT(*) total, SUM(status='publicado') pub, SUM(status='error') err, "
                "SUM(status IN ('pendiente','esperando_aprobacion','reintentando')) pend "
                "FROM queue_items WHERE channel_id=?", (c.id,))
            out.append({"canal": c.name, "idioma": c.language, "total": row["total"] or 0,
                        "publicados": row["pub"] or 0, "errores": row["err"] or 0, "pendientes": row["pend"] or 0})
        return out
