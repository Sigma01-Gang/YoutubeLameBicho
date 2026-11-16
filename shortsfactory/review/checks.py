"""Comprobaciones técnicas automáticas de los vídeos exportados."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import SHORT_MAX_SECONDS
from ..ffmpeg import runner
from ..ffmpeg.commands import build_qc_command


@dataclass
class QCIssue:
    level: str  # error | aviso
    message: str


@dataclass
class QCReport:
    path: str
    info: runner.MediaInfo | None = None
    issues: list[QCIssue] = field(default_factory=list)
    max_volume: float | None = None
    mean_volume: float | None = None
    black: list[tuple[float, float]] = field(default_factory=list)
    frozen: list[tuple[float, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)


def parse_volumedetect(stderr: str) -> tuple[float | None, float | None]:
    mx = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", stderr)
    mn = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", stderr)

    def f(m):
        if not m:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))

    return f(mx), f(mn)


def parse_blackdetect(stderr: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", stderr)]


def parse_freezedetect(stderr: str, total: float | None = None) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else (total if total is not None else s)
        out.append((s, e))
    return out


def evaluate(info: runner.MediaInfo, stderr: str, *, is_short: bool, width: int = 1080, height: int = 1920,
             min_bitrate: int = 1_500_000, max_bitrate: int = 40_000_000, freeze_tail: float = 0.0) -> QCReport:
    """Evalúa la información de ffprobe y los detectores de FFmpeg (función pura)."""
    rep = QCReport(path="", info=info)
    if (info.width, info.height) != (width, height):
        rep.issues.append(QCIssue("error", f"resolución {info.width}x{info.height}, se esperaba {width}x{height}"))
    if is_short and info.duration > SHORT_MAX_SECONDS + 0.05:
        rep.issues.append(QCIssue("error", f"duración {info.duration:.2f}s supera {SHORT_MAX_SECONDS}s para Shorts"))
    if info.duration <= 0.5:
        rep.issues.append(QCIssue("error", "duración nula o casi nula"))
    if info.bitrate and info.bitrate < min_bitrate:
        rep.issues.append(QCIssue("aviso", f"bitrate bajo ({info.bitrate // 1000} kb/s)"))
    if info.bitrate and info.bitrate > max_bitrate:
        rep.issues.append(QCIssue("aviso", f"bitrate muy alto ({info.bitrate // 1000} kb/s)"))
    if not info.has_audio:
        rep.issues.append(QCIssue("error", "sin pista de audio"))
    rep.max_volume, rep.mean_volume = parse_volumedetect(stderr)
    if rep.max_volume is not None and rep.max_volume >= -0.1:
        rep.issues.append(QCIssue("aviso", f"audio saturado (pico {rep.max_volume:.1f} dB)"))
    if rep.mean_volume is not None and rep.mean_volume < -35:
        rep.issues.append(QCIssue("aviso", f"audio demasiado bajo (media {rep.mean_volume:.1f} dB)"))
    rep.black = parse_blackdetect(stderr)
    for a, b in rep.black:
        rep.issues.append(QCIssue("aviso", f"fotogramas negros de {a:.1f}s a {b:.1f}s"))
    rep.frozen = parse_freezedetect(stderr, info.duration)
    for a, b in rep.frozen:
        # el congelado final intencionado (cliffhanger) no se cuenta
        if freeze_tail > 0 and b >= info.duration - 0.2 and a >= info.duration - freeze_tail - 0.6:
            continue
        rep.issues.append(QCIssue("aviso", f"imagen congelada de {a:.1f}s a {b:.1f}s"))
    return rep


def run_checks(tools: runner.Tools, path: str, *, is_short: bool, width: int = 1080, height: int = 1920,
               freeze_tail: float = 0.0) -> QCReport:
    info = runner.media_info(tools, path)
    stderr = runner.run(build_qc_command(tools.ffmpeg, path))
    rep = evaluate(info, stderr, is_short=is_short, width=width, height=height, freeze_tail=freeze_tail)
    rep.path = path
    return rep
