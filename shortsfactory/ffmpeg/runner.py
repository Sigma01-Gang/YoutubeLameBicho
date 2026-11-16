"""Ejecución de FFmpeg/ffprobe con progreso y cancelación. Todo es local."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

_CREATIONFLAGS = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class Cancelled(RuntimeError):
    pass


@dataclass
class Tools:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


_TIME_RE = re.compile(r"out_time_(?:ms|us)=(\d+)")


def run(
    cmd: list[str],
    duration: float | None = None,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float | None = None,
) -> str:
    """Ejecuta un comando ffmpeg. Devuelve stderr. Informa progreso 0..1 si se da `duration`.

    Si el comando contiene "-progress pipe:1" se lee el progreso de stdout.
    """
    log.debug("Ejecutando: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATIONFLAGS,
    )
    stderr_chunks: list[str] = []

    def _read_err() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    t = threading.Thread(target=_read_err, daemon=True)
    t.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled("tarea cancelada")
        if progress and duration:
            m = _TIME_RE.search(line)
            if m:
                secs = int(m.group(1)) / 1_000_000
                progress(max(0.0, min(1.0, secs / duration)))
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise FFmpegError("tiempo de espera agotado")
    t.join(timeout=5)
    if cancel is not None and cancel.is_set():
        raise Cancelled("tarea cancelada")
    stderr = "".join(stderr_chunks)
    if proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"ffmpeg terminó con código {proc.returncode}:\n{tail}", stderr)
    if progress:
        progress(1.0)
    return stderr


def run_capture(cmd: list[str], timeout: float | None = 600) -> tuple[str, str]:
    """Ejecuta un comando y devuelve (stdout, stderr), lanzando FFmpegError si falla."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"falló: {' '.join(cmd[:3])}…\n{proc.stderr[-1500:]}", proc.stderr)
    return proc.stdout, proc.stderr


def probe(tools: Tools, path: str) -> dict:
    out, _ = run_capture(
        [tools.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        timeout=120,
    )
    return json.loads(out)


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    bitrate: int
    vcodec: str
    acodec: str


def parse_probe(data: dict) -> MediaInfo:
    """Convierte la salida JSON de ffprobe en MediaInfo (función pura)."""
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    fps = 0.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    return MediaInfo(
        duration=duration,
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        fps=fps,
        has_audio=a is not None,
        bitrate=int(fmt.get("bit_rate") or 0),
        vcodec=v.get("codec_name", ""),
        acodec=(a or {}).get("codec_name", ""),
    )


def media_info(tools: Tools, path: str) -> MediaInfo:
    return parse_probe(probe(tools, path))


def ffmpeg_version(tools: Tools) -> str:
    try:
        out, _ = run_capture([tools.ffmpeg, "-hide_banner", "-version"], timeout=20)
        return out.splitlines()[0] if out else ""
    except (OSError, FFmpegError, subprocess.TimeoutExpired):
        return ""


def available_encoders(tools: Tools) -> set[str]:
    try:
        out, _ = run_capture([tools.ffmpeg, "-hide_banner", "-encoders"], timeout=20)
    except (OSError, FFmpegError):
        return set()
    return parse_encoders(out)


def parse_encoders(text: str) -> set[str]:
    encs = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            encs.add(parts[1])
    return encs


def available_filters(tools: Tools) -> set[str]:
    try:
        out, _ = run_capture([tools.ffmpeg, "-hide_banner", "-filters"], timeout=20)
    except (OSError, FFmpegError):
        return set()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return names
