"""Editor de subtítulos integrado: texto, tiempos, dividir/unir, desfase, avisos y glosario."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.timeutil import format_timestamp, parse_timestamp
from ..subtitles import srt


class SubtitleEditor(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.path: str | None = None
        self.cues: list[srt.Cue] = []
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, fn in (("Abrir SRT/VTT", self.open), ("Guardar", self.save), ("Guardar como…", self.save_as),
                         ("Dividir línea", self.split), ("Unir con siguiente", self.merge),
                         ("Añadir línea", self.add), ("Borrar línea", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)
        bar2 = QHBoxLayout()
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-3600, 3600)
        self.offset.setDecimals(3)
        self.offset.setSingleStep(0.1)
        b = QPushButton("Aplicar desfase global")
        b.clicked.connect(self.apply_offset)
        self.max_chars = QSpinBox()
        self.max_chars.setRange(10, 100)
        self.max_chars.setValue(32)
        self.max_lines = QSpinBox()
        self.max_lines.setRange(1, 5)
        self.max_lines.setValue(2)
        chk = QPushButton("Comprobar")
        chk.clicked.connect(self.check)
        bar2.addWidget(QLabel("Desfase (s):"))
        bar2.addWidget(self.offset)
        bar2.addWidget(b)
        bar2.addSpacing(20)
        bar2.addWidget(QLabel("Máx. caracteres/línea:"))
        bar2.addWidget(self.max_chars)
        bar2.addWidget(QLabel("Máx. líneas:"))
        bar2.addWidget(self.max_lines)
        bar2.addWidget(chk)
        bar2.addStretch()
        lay.addLayout(bar2)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Inicio", "Fin", "Texto"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 3)
        lay.addWidget(QLabel("Avisos (longitud, solapes y glosario de nombres propios):"))
        self.warnings = QListWidget()
        self.warnings.itemDoubleClicked.connect(lambda it: self.table.selectRow(it.data(Qt.UserRole)))
        lay.addWidget(self.warnings, 1)
        self._loading = False

    def open(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Abrir subtítulos", "", "Subtítulos (*.srt *.vtt)")
        if not path:
            return
        self.path = path
        self.cues = srt.load(path)
        self.refresh()

    def refresh(self):
        self._loading = True
        self.table.setRowCount(len(self.cues))
        for i, c in enumerate(self.cues):
            self.table.setItem(i, 0, QTableWidgetItem(format_timestamp(c.start)))
            self.table.setItem(i, 1, QTableWidgetItem(format_timestamp(c.end)))
            self.table.setItem(i, 2, QTableWidgetItem(c.text.replace("\n", " / ")))
        self._loading = False
        self.check()

    def _edited(self, item: QTableWidgetItem):
        if self._loading:
            return
        i, col = item.row(), item.column()
        try:
            if col == 0:
                self.cues[i].start = parse_timestamp(item.text())
            elif col == 1:
                self.cues[i].end = parse_timestamp(item.text())
            else:
                self.cues[i].text = item.text().replace(" / ", "\n")
        except ValueError as exc:
            QMessageBox.warning(self, "Tiempo no válido", str(exc))
        self.check()

    def _row(self) -> int:
        return self.table.currentRow()

    def split(self):
        if self._row() >= 0:
            self.cues = srt.split_cue(self.cues, self._row())
            self.refresh()

    def merge(self):
        if self._row() >= 0:
            self.cues = srt.merge_cues(self.cues, self._row())
            self.refresh()

    def add(self):
        t = self.cues[-1].end + 0.1 if self.cues else 0.0
        self.cues.append(srt.Cue(t, t + 2, "Nueva línea"))
        self.refresh()

    def delete(self):
        if self._row() >= 0:
            del self.cues[self._row()]
            self.refresh()

    def apply_offset(self):
        self.cues = srt.shift(self.cues, self.offset.value())
        self.refresh()

    def check(self):
        self.warnings.clear()
        glossary = self.ctx.db.get_setting("glossary", []) or []
        warns = srt.validate(self.cues, self.max_chars.value(), self.max_lines.value())
        warns += srt.check_glossary(self.cues, glossary)
        bad_rows = set()
        for w in warns:
            self.warnings.addItem(f"Línea {w.index + 1}: {w.message}")
            self.warnings.item(self.warnings.count() - 1).setData(Qt.UserRole, w.index)
            bad_rows.add(w.index)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 2)
            if it:
                it.setBackground(QColor(120, 60, 0, 120) if i in bad_rows else QColor(0, 0, 0, 0))

    def save(self):
        if not self.path:
            return self.save_as()
        srt.save(self.cues, self.path)

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Guardar subtítulos", self.path or "subtitulos.srt",
                                              "SRT (*.srt);;WebVTT (*.vtt)")
        if path:
            self.path = path
            srt.save(self.cues, path)
