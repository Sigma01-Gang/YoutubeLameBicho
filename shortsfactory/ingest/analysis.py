"""Análisis local de vídeos con FFmpeg: silencios, planos, movimiento, forma de onda."""
from __future__ import annotations

import subprocess
import threading

from ..audio.filters import silencedetect_command, waveform_pcm_command
from ..ffmpeg import runner
from ..ffmpeg.commands import build_motion_command, build_scene_command
from ..framing.filters import cropdetect_command, parse_cropdetect
from .cutting import Interval, parse_motion_scores, parse_scene_times, parse_silencedetect, rms_windows


def detect_silences(tools: runner.Tools, src: str, noise_db: float = -35, min_dur: float = 0.4,
                    total: float | None = None, cancel: threading.Event | None = None) -> list[Interval]:
    stderr = runner.run(silencedetect_command(tools.ffmpeg, src, noise_db, min_dur), cancel=cancel)
    return parse_silencedetect(stderr, total)


def detect_scenes(tools: runner.Tools, src: str, threshold: float = 0.35,
                  cancel: threading.Event | None = None) -> list[float]:
    stderr = runner.run(build_scene_command(tools.ffmpeg, src, threshold), cancel=cancel)
    return parse_scene_times(stderr)


def motion_profile(tools: runner.Tools, src: str) -> list[tuple[float, float]]:
    out, _ = runner.run_capture(build_motion_command(tools.ffmpeg, src), timeout=3600)
    return parse_motion_scores(out)


def pcm_samples(tools: runner.Tools, src: str, rate: int = 4000):
    import numpy as np

    proc = subprocess.run(waveform_pcm_command(tools.ffmpeg, src, rate), capture_output=True,
                          stdin=subprocess.DEVNULL, creationflags=runner._CREATIONFLAGS)
    if proc.returncode != 0:
        raise runner.FFmpegError("no se pudo extraer el audio", proc.stderr.decode("utf-8", "replace"))
    return np.frombuffer(proc.stdout, dtype=np.int16)


def waveform_peaks(samples, buckets: int = 4000) -> list[float]:
    """Picos normalizados 0..1 para dibujar la forma de onda."""
    import numpy as np

    arr = np.abs(np.asarray(samples, dtype=np.float32))
    if not len(arr):
        return []
    buckets = max(1, min(buckets, len(arr)))
    trimmed = arr[: len(arr) - len(arr) % buckets]
    peaks = trimmed.reshape(buckets, -1).max(axis=1) if len(trimmed) else arr
    m = float(peaks.max()) or 1.0
    return (peaks / m).tolist()


def loudness_profile(samples, rate: int = 4000) -> list[tuple[float, float]]:
    return rms_windows(samples, rate, 1.0)


def detect_black_bars(tools: runner.Tools, src: str, duration: float) -> tuple[int, int, int, int] | None:
    start = max(0.0, min(duration * 0.2, duration - 20))
    stderr = runner.run(cropdetect_command(tools.ffmpeg, src, start, min(20.0, duration)))
    return parse_cropdetect(stderr)
