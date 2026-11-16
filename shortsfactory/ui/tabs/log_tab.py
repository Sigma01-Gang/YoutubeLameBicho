"""Pestaña Registro: registros de la app y de subidas, exportación y panel de estadísticas."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


class BarChart(QWidget):
    """Gráfico de barras sencillo: vídeos publicados por día."""

    def __init__(self):
        super().__init__()
        self.data: list[tuple[str, int]] = []
        self.setMinimumHeight(160)

    def set_data(self, data):
        self.data = data
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        text = self.palette().text().color()
        if not self.data:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignCenter, "Sin publicaciones en el periodo")
            return
        mx = max(v for _, v in self.data) or 1
        bw = w / len(self.data)
        for i, (label, v) in enumerate(self.data):
            bh = (h - 40) * v / mx
            r = QRectF(i * bw + bw * 0.15, h - 22 - bh, bw * 0.7, bh)
            p.fillRect(r, QColor(79, 195, 247))
            p.setPen(text)
            p.drawText(QRectF(i * bw, h - 20, bw, 18), Qt.AlignCenter, label[5:])
            p.drawText(QRectF(i * bw, h - 40 - bh, bw, 16), Qt.AlignCenter, str(v))


class LogTab(QWidget):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Vertical)
        lay.addWidget(split)

        logs = QWidget()
        ll = QVBoxLayout(logs)
        bar = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItems(["Aplicación", "Subidas"])
        self.source.currentIndexChanged.connect(self.refresh)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtrar…")
        self.filter.textChanged.connect(self.refresh)
        bar.addWidget(self.source)
        bar.addWidget(self.filter, 1)
        for text, fn in (("Actualizar", self.refresh), ("Exportar registro…", self.export_log),
                         ("Resumen diario…", self.export_summary)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        ll.addLayout(bar)
        self.table = QTableWidget(0, 4)
        self.table.horizontalHeader().setStretchLastSection(True)
        ll.addWidget(self.table)
        split.addWidget(logs)

        stats = QGroupBox("Estadísticas")
        sl = QHBoxLayout(stats)
        self.chart = BarChart()
        sl.addWidget(self.chart, 2)
        self.kpis = QLabel()
        self.kpis.setAlignment(Qt.AlignTop)
        sl.addWidget(self.kpis, 1)
        self.per_channel = QTableWidget(0, 6)
        self.per_channel.setHorizontalHeaderLabels(["Canal", "Idioma", "Total", "Publicados", "Errores", "Pendientes"])
        sl.addWidget(self.per_channel, 2)
        split.addWidget(stats)
        self.refresh()

    def refresh(self):
        q = f"%{self.filter.text().strip()}%"
        if self.source.currentIndex() == 0:
            rows = self.ctx.db.query("SELECT at, level, logger, message FROM app_log WHERE message LIKE ? OR logger LIKE ? "
                                     "ORDER BY id DESC LIMIT 2000", (q, q))
            self.table.setHorizontalHeaderLabels(["Fecha", "Nivel", "Origen", "Mensaje"])
        else:
            rows = self.ctx.db.query("SELECT u.at, c.name, u.event, u.detail FROM upload_log u LEFT JOIN channels c "
                                     "ON c.id=u.channel_id WHERE u.detail LIKE ? OR u.event LIKE ? "
                                     "ORDER BY u.id DESC LIMIT 2000", (q, q))
            self.table.setHorizontalHeaderLabels(["Fecha", "Canal", "Evento", "Detalle"])
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for c in range(4):
                it = QTableWidgetItem(str(r[c] or ""))
                if str(r[1]) in ("ERROR", "CRITICAL") or str(r[2]) in ("error",):
                    it.setForeground(QColor(239, 83, 80))
                self.table.setItem(i, c, it)
        st = self.ctx.stats()
        self.chart.set_data([(d["dia"], d["n"]) for d in st["per_day"]])
        self.kpis.setText(f"<b>Tasa de errores de subida:</b> {st['error_rate'] * 100:.1f}%<br>"
                          f"<b>Tiempo medio de procesado:</b> {st['avg_render_seconds']:.1f} s<br>"
                          f"<b>Renders:</b> {st['renders']} (fallidos {st['render_failures']})")
        chs = st["channels"]
        self.per_channel.setRowCount(len(chs))
        for i, c in enumerate(chs):
            for j, k in enumerate(("canal", "idioma", "total", "publicados", "errores", "pendientes")):
                self.per_channel.setItem(i, j, QTableWidgetItem(str(c[k])))

    def append_live(self, line: str):
        if self.source.currentIndex() == 0 and not self.filter.text():
            self.table.insertRow(0)
            parts = line.split("\t", 3)
            for c, v in enumerate(parts + [""] * (4 - len(parts))):
                self.table.setItem(0, c, QTableWidgetItem(v))

    def export_log(self):
        f, _ = QFileDialog.getSaveFileName(self, "Exportar registro", "registro.txt", "Texto (*.txt);;CSV (*.csv)")
        if f:
            self.ctx.export_log(f)

    def export_summary(self):
        f, _ = QFileDialog.getSaveFileName(self, "Resumen diario", f"resumen_{date.today()}.txt",
                                           "Texto (*.txt);;CSV (*.csv)")
        if f:
            self.ctx.daily_summary(f)
