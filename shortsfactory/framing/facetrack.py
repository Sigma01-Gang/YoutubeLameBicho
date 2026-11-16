"""Seguimiento de cara con el Haar Cascade incluido en OpenCV (sin modelos externos)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..core.models import Segment
from ..subtitles.srt import map_time

log = logging.getLogger(__name__)


def smooth_track(points: list[tuple[float, float]], alpha: float = 0.15, deadzone: float = 0.03) -> list[tuple[float, float]]:
    """Suaviza las posiciones (media exponencial + zona muerta) para evitar saltos de cámara."""
    out: list[tuple[float, float]] = []
    cur = None
    for t, x in points:
        if cur is None:
            cur = x
        elif abs(x - cur) > deadzone:
            cur = cur + alpha * (x - cur)
        out.append((t, cur))
    return out


def crop_fraction(face_cx: float, src_w: int, src_h: int, out_w: int, out_h: int) -> float:
    """Fracción f para crop x=(iw-ow)*f que centra la cara (face_cx en 0..1 del ancho de origen)."""
    scale = max(out_w / src_w, out_h / src_h)
    iw = src_w * scale
    if iw - out_w < 1:
        return 0.5
    f = (face_cx * iw - out_w / 2) / (iw - out_w)
    return min(max(f, 0.0), 1.0)


def write_sendcmd(track: list[tuple[float, float]], path: str | Path, target: str = "crop@face") -> Path:
    lines = [f"{t:.3f} {target} x (iw-ow)*{f:.4f};" for t, f in track]
    p = Path(path)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def detect_faces(src: str, seg: Segment, sample_fps: float = 4.0,
                 cancel: threading.Event | None = None) -> tuple[list[tuple[float, float]], int, int]:
    """Devuelve [(t_origen, centro_x 0..1)], ancho y alto del origen."""
    import cv2

    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(str(cascade_path))
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"no se puede abrir {src}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    pts: list[tuple[float, float]] = []
    last = 0.5
    t = seg.start
    step = 1.0 / sample_fps
    try:
        while t < seg.end:
            if cancel is not None and cancel.is_set():
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, (max(1, w // 3), max(1, h // 3)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
                last = (x + fw / 2) / small.shape[1]
            pts.append((t, last))
            t += step
    finally:
        cap.release()
    return pts, w, h


def build_face_track(src: str, seg: Segment, out_w: int, out_h: int, cmd_path: str | Path,
                     cancel: threading.Event | None = None) -> Path:
    raw, w, h = detect_faces(src, seg, cancel=cancel)
    smoothed = smooth_track(raw)
    kept = seg.kept_intervals()
    track = []
    for t, cx in smoothed:
        out_t = map_time(t, kept, seg.speed or 1.0)
        if out_t is not None:
            track.append((out_t, crop_fraction(cx, w, h, out_w, out_h)))
    if not track:
        track = [(0.0, 0.5)]
    return write_sendcmd(track, cmd_path)
