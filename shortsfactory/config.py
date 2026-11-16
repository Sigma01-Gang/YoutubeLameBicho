"""Rutas de la aplicación y ajustes por defecto."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ShortsFactory"


def data_dir() -> Path:
    """Carpeta de datos de la app (base de datos, perfiles de navegador, caché, registros)."""
    override = os.environ.get("SHORTSFACTORY_DATA")
    if override:
        p = Path(override)
    elif sys.platform.startswith("win"):
        p = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub_dir(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def bundled_tool_dirs() -> list[Path]:
    """Carpetas donde el instalador deja FFmpeg (se prefieren a la versión del PATH)."""
    dirs = []
    if os.environ.get("SHORTSFACTORY_FFMPEG_DIR"):
        dirs.append(Path(os.environ["SHORTSFACTORY_FFMPEG_DIR"]))
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / APP_NAME / "ffmpeg" / "bin")
    dirs.append(Path(__file__).resolve().parents[1] / "ffmpeg" / "bin")
    return dirs


def resolve_tool(name: str, configured: str | None) -> str:
    """Ruta del ejecutable: la configurada en Ajustes; si es la de por defecto, la incluida por el instalador."""
    configured = (configured or "").strip()
    if configured and configured != name:
        return configured
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    for d in bundled_tool_dirs():
        if (d / exe).is_file():
            return str(d / exe)
    return configured or name


DEFAULT_SETTINGS: dict = {
    "ffmpeg_path": "ffmpeg",
    "ffprobe_path": "ffprobe",
    "output_dir": "",
    "theme": "dark",
    "part_seconds": 180,
    "short_min": 15,
    "short_max": 60,
    "clean_cuts": True,
    "clean_cut_tolerance": 8.0,
    "silence_db": -35,
    "silence_min": 0.4,
    "pause_threshold": 1.2,
    "scene_threshold": 0.35,
    "upload_interval_hours": 3.0,
    "approval_mode": True,
    "max_retries": 3,
    "retry_base_minutes": 5,
    "threads": 2,
    "hwaccel": "auto",  # auto | none | nvenc | qsv | amf
    "test_mode": False,  # subir como privado/no listado
    "test_visibility": "PRIVATE",
    "cache_enabled": True,
    "backup_keep": 10,
    "first_run_done": False,
    "simulation_mode": False,
    "global_pause": False,
    "review_checklist": [
        "El audio se escucha bien en todo el vídeo",
        "Los subtítulos están sincronizados y sin faltas",
        "El encuadre no corta caras ni texto importante",
        "Título y descripción correctos",
        "El final deja intriga para la siguiente parte",
    ],
    "shortcuts": {
        "mark_in": "I",
        "mark_out": "O",
        "frame_next": "Right",
        "frame_prev": "Left",
        "second_next": "Shift+Right",
        "second_prev": "Shift+Left",
        "play_pause": "Space",
        "add_segment": "Ctrl+Return",
        "search": "Ctrl+F",
    },
    "render_defaults": {},
    "subtitle_presets": {},
    "glossary": [],
    "blocks": {
        "suscripcion": "🔔 Suscríbete para no perderte la siguiente parte.",
        "creditos": "",
        "enlaces": "",
    },
}

SHORT_MAX_SECONDS = 60
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX_TOTAL = 500
