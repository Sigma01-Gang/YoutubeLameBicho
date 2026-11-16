"""Línea de tiempo con forma de onda, segmentos, marcas de entrada/salida y cabezal."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import Segment
from ..core.timeutil import format_short


class Timeline(QWidget):
    seek = Signal(float)
    segment_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(130)
        self.setFocusPolicy(Qt.StrongFocus)
        self.duration = 0.0
        self.peaks: list[float] = []
        self.segments: list[Segment] = []
        self.scenes: list[float] = []
        self.playhead = 0.0
        self.mark_in: float | None = None
        self.mark_out: float | None = None
        self.selected = -1
        self.zoom = 1.0
        self.offset = 0.0  # segundos en el borde izquierdo

    # --- datos -----------------------------------------------------------------
    def set_media(self, duration: float, peaks: list[float]) -> None:
        self.duration, self.peaks = duration, peaks
        self.zoom, self.offset = 1.0, 0.0
        self.update()

    def set_segments(self, segs: list[Segment]) -> None:
        self.segments = segs
        self.update()

    def set_playhead(self, t: float) -> None:
        self.playhead = t
        visible = self.duration / self.zoom if self.duration else 0
        if visible and not (self.offset <= t <= self.offset + visible):
            self.offset = max(0.0, min(t - visible * 0.1, self.duration - visible))
        self.update()

    # --- conversión ----------------------------------------------------------
    def _visible(self) -> float:
        return max(0.001, self.duration / self.zoom)

    def t_to_x(self, t: float) -> float:
        return (t - self.offset) / self._visible() * self.width()

    def x_to_t(self, x: float) -> float:
        return min(max(self.offset + x / max(1, self.width()) * self._visible(), 0.0), self.duration)

    # --- dibujo --------------------------------------------------------------
    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        if self.duration <= 0:
            p.setPen(pal.text().color())
            p.drawText(self.rect(), Qt.AlignCenter, "Importa un vídeo para ver la forma de onda")
            return
        # segmentos
        for i, s in enumerate(self.segments):
            x1, x2 = self.t_to_x(s.start), self.t_to_x(s.end)
            color = QColor(255, 170, 0, 90) if i == self.selected else QColor(79, 195, 247, 55)
            p.fillRect(QRectF(x1, 0, max(1, x2 - x1), h), color)
            p.setPen(QPen(QColor(79, 195, 247), 1))
            p.drawLine(QPointF(x1, 0), QPointF(x1, h))
            p.drawText(QPointF(x1 + 3, 12), s.label or str(i + 1))
            if s.keep:
                prev = s.start
                for a, b in s.keep:
                    if a > prev:
                        p.fillRect(QRectF(self.t_to_x(prev), h - 10, self.t_to_x(a) - self.t_to_x(prev), 10),
                                   QColor(244, 67, 54, 160))
                    prev = b
        # forma de onda
        mid = h / 2
        p.setPen(QPen(QColor(129, 212, 250), 1))
        n = len(self.peaks)
        if n:
            for x in range(w):
                t = self.x_to_t(x)
                idx = min(n - 1, int(t / self.duration * n))
                amp = self.peaks[idx] * (h * 0.42)
                p.drawLine(QPointF(x, mid - amp), QPointF(x, mid + amp))
        # cambios de plano
        p.setPen(QPen(QColor(156, 39, 176), 1, Qt.DashLine))
        for t in self.scenes:
            x = self.t_to_x(t)
            p.drawLine(QPointF(x, h * 0.75), QPointF(x, h))
        # marcas
        for t, col, txt in ((self.mark_in, QColor(76, 175, 80), "IN"), (self.mark_out, QColor(244, 67, 54), "OUT")):
            if t is not None:
                x = self.t_to_x(t)
                p.setPen(QPen(col, 2))
                p.drawLine(QPointF(x, 0), QPointF(x, h))
                p.drawText(QPointF(x + 3, h - 14), txt)
        # cabezal
        x = self.t_to_x(self.playhead)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.setPen(pal.text().color())
        p.drawText(QPointF(4, h - 2), f"{format_short(self.offset)}")
        p.drawText(QPointF(w - 60, h - 2), f"{format_short(self.offset + self._visible())}")
        p.drawText(QPointF(x + 4, 26), format_short(self.playhead))

    # --- interacción ---------------------------------------------------------
    def mousePressEvent(self, e):  # noqa: N802
        t = self.x_to_t(e.position().x())
        if e.button() == Qt.LeftButton:
            for i, s in enumerate(self.segments):
                if s.start <= t <= s.end and e.modifiers() & Qt.ControlModifier:
                    self.selected = i
                    self.segment_selected.emit(i)
                    break
            self.seek.emit(t)
        elif e.button() == Qt.RightButton:
            self.mark_in = t if self.mark_in is None or self.mark_out is not None else self.mark_in
            if self.mark_out is None and self.mark_in is not None and t > self.mark_in:
                self.mark_out = t
            self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if e.buttons() & Qt.LeftButton:
            self.seek.emit(self.x_to_t(e.position().x()))

    def wheelEvent(self, e):  # noqa: N802
        if not self.duration:
            return
        t = self.x_to_t(e.position().x())
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.zoom = min(max(self.zoom * factor, 1.0), max(1.0, self.duration / 2))
        vis = self._visible()
        self.offset = min(max(0.0, t - (e.position().x() / max(1, self.width())) * vis), self.duration - vis)
        self.update()
