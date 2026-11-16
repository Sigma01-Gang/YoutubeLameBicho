"""Cálculo de cortes: partes fijas, cortes limpios en silencios, capítulos, ranking (funciones puras)."""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import replace

from ..core.models import Segment
from ..core.timeutil import parse_timestamp

Interval = tuple[float, float]


# --- parseo de salidas de FFmpeg ---------------------------------------------

def parse_silencedetect(stderr: str, total: float | None = None) -> list[Interval]:
    """Extrae los intervalos de silencio de la salida de silencedetect."""
    silences: list[Interval] = []
    start: float | None = None
    for line in stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None and total is not None:
        silences.append((start, total))
    return silences


def parse_scene_times(stderr: str) -> list[float]:
    """Tiempos de cambio de plano a partir de showinfo (pts_time)."""
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", stderr)]


def parse_motion_scores(stdout: str) -> list[tuple[float, float]]:
    """Pares (tiempo, puntuación de escena) de metadata=print."""
    out = []
    t = None
    for line in stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and t is not None:
            out.append((t, float(m.group(1))))
    return out


# --- troceado ------------------------------------------------------------------

def split_fixed(duration: float, size: float, min_tail: float = 0.0, kind: str = "part") -> list[Segment]:
    """Trocea en bloques de `size` segundos. Si la última parte dura menos de `min_tail`, se une a la anterior."""
    if duration <= 0 or size <= 0:
        return []
    n = max(1, math.ceil(duration / size - 1e-9))
    bounds = [min(i * size, duration) for i in range(n)] + [duration]
    segs = [Segment(bounds[i], bounds[i + 1], kind=kind) for i in range(n)]
    if len(segs) > 1 and segs[-1].duration < min_tail:
        last = segs.pop()
        segs[-1] = replace(segs[-1], end=last.end)
    return label_parts(segs)


def label_parts(segs: list[Segment], fmt: str = "Parte {n}/{total}") -> list[Segment]:
    total = len(segs)
    return [replace(s, label=fmt.format(n=i + 1, total=total)) for i, s in enumerate(segs)]


def nearest_silence_point(target: float, silences: list[Interval], tolerance: float) -> float | None:
    """Punto de corte (centro del silencio) más cercano al objetivo, dentro de la tolerancia."""
    best, best_d = None, None
    for a, b in silences:
        if a <= target <= b:
            return target  # el objetivo ya cae en un silencio
        mid = (a + b) / 2
        d = abs(mid - target)
        if d <= tolerance and (best_d is None or d < best_d):
            best, best_d = mid, d
    return best


def split_clean(duration: float, size: float, silences: list[Interval], tolerance: float = 8.0,
                min_tail: float = 0.0, kind: str = "part") -> list[Segment]:
    """Como split_fixed, pero mueve cada corte al silencio más cercano (si lo hay dentro de la tolerancia)."""
    cuts: list[float] = []
    pos = 0.0
    while duration - pos > size + 1e-9:
        target = pos + size
        snap = nearest_silence_point(target, silences, tolerance)
        cut = snap if snap is not None and snap > pos + 1.0 else target
        cuts.append(cut)
        pos = cut
    bounds = [0.0] + cuts + [duration]
    segs = [Segment(bounds[i], bounds[i + 1], kind=kind) for i in range(len(bounds) - 1)]
    if len(segs) > 1 and segs[-1].duration < min_tail:
        last = segs.pop()
        segs[-1] = replace(segs[-1], end=last.end)
    return label_parts(segs)


def split_shorts(duration: float, target: float, silences: list[Interval] | None = None,
                 min_len: float = 15.0, max_len: float = 60.0) -> list[Segment]:
    """Cortes para Shorts, nunca por encima de max_len segundos."""
    target = min(max(target, min_len), max_len)
    tol = max(0.0, min(max_len - target, target - min_len, 5.0))
    segs = split_clean(duration, target, silences or [], tolerance=tol, min_tail=min_len, kind="short")
    fixed: list[Segment] = []
    for s in segs:
        if s.duration > max_len:
            for x in split_fixed(s.duration, target):
                fixed.append(Segment(x.start + s.start, x.end + s.start, kind="short"))
        else:
            fixed.append(s)
    return label_parts([s for s in fixed if s.duration >= min(min_len, duration)], fmt="Short {n}")


# --- capítulos y marcas --------------------------------------------------------

_CHAPTER_RE = re.compile(r"^\s*\[?((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\]?\s*[-–—:]?\s*(.*)$")


def parse_chapters_txt(text: str, total: float | None = None) -> list[Segment]:
    """Formato "00:00:00 Título" por línea. Cada capítulo termina donde empieza el siguiente."""
    marks: list[tuple[float, str]] = []
    for line in text.splitlines():
        m = _CHAPTER_RE.match(line)
        if m:
            marks.append((parse_timestamp(m.group(1).replace(",", ".")), m.group(2).strip()))
    marks.sort()
    segs = []
    for i, (t, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else (total if total is not None else t)
        if end > t:
            segs.append(Segment(t, end, label=title or f"Capítulo {i + 1}"))
    return segs


def parse_marks_csv(text: str) -> list[Segment]:
    """CSV con columnas inicio,fin[,titulo] (o start,end,title)."""
    reader = csv.DictReader(io.StringIO(text))
    segs = []
    for row in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        s = r.get("inicio") or r.get("start")
        e = r.get("fin") or r.get("end")
        if not s or not e:
            continue
        segs.append(Segment(parse_timestamp(s), parse_timestamp(e), label=r.get("titulo") or r.get("title") or ""))
    return segs


# --- ajustes de segmento -------------------------------------------------------

def _trim_intervals(kept: list[Interval], budget: float, from_start: bool) -> list[Interval]:
    """Conserva `budget` segundos de los intervalos, recortando por el final (o por el principio)."""
    seq = list(reversed(kept)) if from_start else list(kept)
    out: list[Interval] = []
    left = budget
    for a, b in seq:
        if left <= 1e-9:
            break
        d = b - a
        if d <= left:
            out.append((a, b))
            left -= d
        else:
            out.append((b - left, b) if from_start else (a, a + left))
            left = 0
    return sorted(out)


def fit_to_duration(seg: Segment, target: float, mode: str = "end") -> Segment:
    """Ajusta la duración FINAL del clip (tras pausas y velocidad) a `target` recortando las colas.

    mode: 'end' recorta el final, 'start' el principio y 'both' reparte entre ambos extremos.
    """
    speed = seg.speed or 1.0
    if seg.output_duration() <= target + 1e-9:
        return replace(seg)
    budget = target * speed  # segundos de origen que caben
    kept = [(max(a, seg.start), min(b, seg.end)) for a, b in seg.kept_intervals() if min(b, seg.end) > max(a, seg.start)]
    if mode == "both":
        total = sum(b - a for a, b in kept)
        drop = (total - budget) / 2
        kept = _trim_intervals(kept, total - drop, from_start=True)
        kept = _trim_intervals(kept, budget, from_start=False)
    else:
        kept = _trim_intervals(kept, budget, from_start=(mode == "start"))
    new = replace(seg, start=kept[0][0], end=kept[-1][1])
    return replace(new, keep=kept if seg.keep else None)


def duplicate_with_duration(seg: Segment, new_duration: float, source_duration: float | None = None) -> Segment:
    end = seg.start + new_duration
    if source_duration is not None:
        end = min(end, source_duration)
    return replace(seg, end=end, label=f"{seg.label} ({int(round(end - seg.start))}s)".strip())


def trim_edge_silence(seg: Segment, silences: list[Interval], pad: float = 0.15) -> Segment:
    """Elimina silencio al principio y al final ajustando los límites (vídeo y audio a la vez)."""
    start, end = seg.start, seg.end
    for a, b in silences:
        if a <= start + 0.05 and b > start:
            start = min(max(start, b - pad), end)
        if b >= end - 0.05 and a < end:
            end = max(min(end, a + pad), start)
    if end - start < 0.5:
        return replace(seg)
    return replace(seg, start=start, end=end)


def remove_pauses(seg: Segment, silences: list[Interval], threshold: float = 1.2, keep_pad: float = 0.25) -> Segment:
    """Quita del segmento los silencios más largos que `threshold` dejando `keep_pad` a cada lado."""
    kept: list[Interval] = []
    cursor = seg.start
    for a, b in sorted(silences):
        a, b = max(a, seg.start), min(b, seg.end)
        if b - a < threshold:
            continue
        cut_a, cut_b = a + keep_pad, b - keep_pad
        if cut_a > cursor:
            kept.append((cursor, cut_a))
        cursor = max(cursor, cut_b)
    if cursor < seg.end:
        kept.append((cursor, seg.end))
    kept = [(a, b) for a, b in kept if b - a > 0.05]
    if len(kept) <= 1 and kept and abs(kept[0][0] - seg.start) < 1e-6 and abs(kept[0][1] - seg.end) < 1e-6:
        return replace(seg, keep=None)
    return replace(seg, keep=kept or None)


# --- ranking de candidatos -----------------------------------------------------

def _mean_in(values: list[tuple[float, float]], a: float, b: float) -> float:
    sel = [v for t, v in values if a <= t < b]
    return sum(sel) / len(sel) if sel else 0.0


def candidates_from_scenes(scene_times: list[float], duration: float, min_len: float = 15.0,
                           max_len: float = 60.0) -> list[Segment]:
    """Genera candidatos que empiezan y terminan en cambios de plano y duran entre min_len y max_len."""
    points = sorted({0.0, *[t for t in scene_times if 0 < t < duration], duration})
    cands: list[Segment] = []
    for i, a in enumerate(points):
        for b in points[i + 1:]:
            d = b - a
            if d < min_len:
                continue
            if d > max_len:
                break
            cands.append(Segment(a, b, kind="short"))
    return cands


def score_candidates(cands: list[Segment], loudness: list[tuple[float, float]], motion: list[tuple[float, float]],
                     ideal: float = 45.0, weights: tuple[float, float, float] = (0.4, 0.4, 0.2),
                     top: int | None = 20) -> list[Segment]:
    """Puntúa por volumen medio, movimiento medio y cercanía a la duración ideal. Devuelve ranking."""
    if not cands:
        return []
    raw = []
    for c in cands:
        raw.append((_mean_in(loudness, c.start, c.end), _mean_in(motion, c.start, c.end), c))
    max_l = max((r[0] for r in raw), default=0) or 1.0
    max_m = max((r[1] for r in raw), default=0) or 1.0
    wl, wm, wd = weights
    scored = []
    for lo, mo, c in raw:
        dur_score = max(0.0, 1.0 - abs(c.duration - ideal) / max(ideal, 1.0))
        s = wl * (lo / max_l) + wm * (mo / max_m) + wd * dur_score
        scored.append(replace(c, score=round(s, 4)))
    scored.sort(key=lambda s: s.score or 0, reverse=True)
    # elimina solapes fuertes para que el ranking sea variado
    picked: list[Segment] = []
    for s in scored:
        if all(min(s.end, p.end) - max(s.start, p.start) < 0.5 * s.duration for p in picked):
            picked.append(s)
        if top and len(picked) >= top:
            break
    return [replace(s, label=f"#{i + 1} ({s.score:.2f})") for i, s in enumerate(picked)]


def rms_windows(samples, rate: int, window: float = 1.0) -> list[tuple[float, float]]:
    """RMS por ventana a partir de muestras PCM (secuencia de enteros o array numpy)."""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    n = max(1, int(rate * window))
    out = []
    for i in range(0, len(arr), n):
        chunk = arr[i:i + n]
        if len(chunk):
            out.append((i / rate, float(np.sqrt(np.mean(chunk * chunk)))))
    return out
