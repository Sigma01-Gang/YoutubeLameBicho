"""Utilidades de tiempo: parseo y formato de marcas de tiempo."""
from __future__ import annotations

import math
import re
from datetime import datetime

_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?\s*$")


def parse_timestamp(value: str | float | int) -> float:
    """Convierte "HH:MM:SS(.mmm)", "MM:SS" o segundos a segundos (float)."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        raise ValueError("marca de tiempo vacía")
    try:
        return float(s.replace(",", "."))
    except ValueError:
        pass
    m = _TS_RE.match(s)
    if not m:
        raise ValueError(f"marca de tiempo no válida: {value!r}")
    h, mi, se, ms = m.groups()
    total = int(h or 0) * 3600 + int(mi) * 60 + int(se)
    if ms:
        total += int(ms.ljust(3, "0")) / 1000.0
    return float(total)


def format_timestamp(seconds: float, sep: str = ".", always_hours: bool = True) -> str:
    """Formatea segundos como HH:MM:SS.mmm (sep="," para SRT)."""
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if always_hours or h:
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"
    return f"{m:02d}:{s:02d}{sep}{ms:03d}"


def format_short(seconds: float) -> str:
    """Formato legible corto: 1:02:03 o 2:03."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def snap_to_frame(t: float, fps: float) -> float:
    """Ajusta un tiempo al inicio del fotograma más cercano."""
    if fps <= 0:
        return t
    return round(t * fps) / fps


def step_frames(t: float, frames: int, fps: float) -> float:
    """Avanza (o retrocede) un número de fotogramas exacto."""
    frame = math.floor(t * fps + 1e-6) + frames
    return max(0.0, frame / fps)


DATE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%Y/%m/%d %H:%M",
)


def parse_datetime(value) -> datetime | None:
    """Parsea fechas de planilla. Devuelve None si está vacía. Lanza ValueError si no es válida."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(second=0, microsecond=0)
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError(f"fecha no válida: {s!r}") from exc
