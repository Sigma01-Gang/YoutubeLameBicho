"""Pestaña Planillas: varias fuentes (una por canal), validación, reglas, vista previa e historial."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from ...core.models import Status
from ...sheets import sheets as sh

COLS = ["id", "canal", "archivo_video", "titulo", "fecha_hora_publicacion", "estado", "url_publicado", "grupo",
        "errores"]
EDITABLE = {"titulo", "fecha_hora_publicacion", "grupo"}


class TemplatesDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Plantillas por canal y bloques reutilizables")
        lay = QVBoxLayout(self)
        self.channel = QComboBox()
        for c in ctx.channels.list():
            self.channel.addItem(c.name, c.id)
        self.channel.currentIndexChanged.connect(self.load)
        form = QFormLayout()
        self.title = QPlainTextEdit()
        self.desc = QPlainTextEdit()
        self.tags = QPlainTextEdit()
        self.blocks = QPlainTextEdit()
        for w in (self.title, self.tags):
            w.setMaximumHeight(60)
        form.addRow("Canal", self.channel)
        form.addRow("Título", self.title)
        form.addRow("Descripción", self.desc)
        form.addRow("Etiquetas", self.tags)
        form.addRow("Bloques (JSON)", self.blocks)
        lay.addLayout(form)
        lay.addWidget(QLabel("Variables: {titulo} {parte} {total} {canal} {nombre} {archivo} · "
                             "Bloques: {bloque:suscripcion} {bloque:enlaces} {bloque:creditos}"))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.blocks.setPlainText(json.dumps(ctx.db.get_setting("blocks", {}), ensure_ascii=False, indent=2))
        self.load()

    def load(self):
        ch = self.ctx.channels.get(self.channel.currentData()) if self.channel.currentData() else None
        if ch:
            self.title.setPlainText(ch.templates.get("titulo", ""))
            self.desc.setPlainText(ch.templates.get("descripcion", ""))
            self.tags.setPlainText(ch.templates.get("etiquetas", ""))

    def save(self):
        ch = self.ctx.channels.get(self.channel.currentData()) if self.channel.currentData() else None
        if ch:
            ch.templates = {"titulo": self.title.toPlainText(), "descripcion": self.desc.toPlainText(),
                            "etiquetas": self.tags.toPlainText()}
            self.ctx.channels.update(ch)
        try:
            self.ctx.db.set_setting("blocks", json.loads(self.blocks.toPlainText() or "{}"))
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "JSON no válido", str(exc))
            return
        QMessageBox.information(self, "Guardado", "Plantillas guardadas.")


class SheetsTab(QWidget):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        self.rows: list[dict] = []
        self._loading = False
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, fn in (("Cargar planilla…", self.load_sheet), ("Recargar todas", self.reload_all),
                         ("Descargar plantilla oficial…", self.download_template),
                         ("Emparejar carpeta de vídeos…", self.match_folder), ("Plantillas y bloques…", self.templates),
                         ("Exportar con estados…", self.export), ("Encolar seleccionadas", self.enqueue)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)
        fl = QHBoxLayout()
        self.f_channel = QComboBox()
        self.f_state = QComboBox()
        self.f_state.addItem("(todos los estados)", None)
        for s in Status.values():
            self.f_state.addItem(s, s)
        self.f_order = QComboBox()
        for lab, v in (("Ordenar por fecha", "fecha"), ("Ordenar por estado", "estado"), ("Ordenar por canal", "canal")):
            self.f_order.addItem(lab, v)
        for w in (self.f_channel, self.f_state, self.f_order):
            w.currentIndexChanged.connect(self.refresh)
            fl.addWidget(w)
        self.summary = QLabel()
        fl.addWidget(self.summary, 1)
        lay.addLayout(fl)
        self.sources = QLabel()
        self.sources.setWordWrap(True)
        lay.addWidget(self.sources)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.table.itemChanged.connect(self.on_edit)
        split.addWidget(self.table)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(QLabel("Vista previa (tarjeta):"))
        self.card = QTextEdit()
        self.card.setReadOnly(True)
        sl.addWidget(self.card, 2)
        sl.addWidget(QLabel("Historial de cambios de la fila:"))
        self.history = QListWidget()
        sl.addWidget(self.history, 2)
        sl.addWidget(QLabel("Alertas (multi-idioma y títulos duplicados):"))
        self.alerts = QListWidget()
        sl.addWidget(self.alerts, 1)
        split.addWidget(side)
        split.setSizes([900, 400])
        lay.addWidget(split, 1)
        self.refresh()

    # --------------------------------------------------------------------------
    def refresh(self):
        cur_ch = self.f_channel.currentData()
        self.f_channel.blockSignals(True)
        self.f_channel.clear()
        self.f_channel.addItem("(todos los canales)", None)
        for c in self.ctx.channels.list():
            self.f_channel.addItem(c.name, c.id)
        if cur_ch:
            self.f_channel.setCurrentIndex(max(0, self.f_channel.findData(cur_ch)))
        self.f_channel.blockSignals(False)
        self.rows = self.ctx.sheets.rows(self.f_channel.currentData(), self.f_state.currentData(),
                                         self.f_order.currentData())
        self._loading = True
        self.table.setRowCount(len(self.rows))
        n_err = 0
        for i, r in enumerate(self.rows):
            errs = [e for e in json.loads(r["errors"] or "[]")]
            real = [e for e in errs if not e.startswith("aviso:")]
            n_err += bool(real)
            for c, col in enumerate(COLS):
                val = "; ".join(errs) if col == "errores" else str(r.get(col, ""))
                it = QTableWidgetItem(val)
                if col not in EDITABLE:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if real:
                    it.setBackground(QColor(160, 40, 40, 90))
                elif errs:
                    it.setBackground(QColor(200, 150, 0, 70))
                self.table.setItem(i, c, it)
        self._loading = False
        self.summary.setText(f"{len(self.rows)} filas · {n_err} con errores (no bloquean al resto)")
        srcs = sorted({(c.name, c.sheet_path) for c in self.ctx.channels.list() if c.sheet_path})
        self.sources.setText("Fuentes: " + " · ".join(f"{n}: {Path(p).name}" for n, p in srcs) if srcs
                             else "Sin planillas cargadas.")
        self.alerts.clear()
        for a in self.ctx.channels.translation_alerts():
            self.alerts.addItem(a)
        for t, n in self.ctx.sheets.duplicate_titles():
            self.alerts.addItem(f"Título repetido {n} veces: «{t}»")

    def on_select(self):
        sel = sorted({i.row() for i in self.table.selectedIndexes()})
        if not sel:
            return
        r = self.rows[sel[0]]
        self.card.setPlainText(sh.preview_card(r) + "\n\nEtiquetas: " + ", ".join(sh.split_tags(r["etiquetas"])))
        self.history.clear()
        for h in self.ctx.sheets.history(r["id"]):
            self.history.addItem(f"{h['changed_at']} · {h['field']}: «{h['old_value']}» → «{h['new_value']}»")

    def on_edit(self, item):
        if self._loading:
            return
        col = COLS[item.column()]
        r = self.rows[item.row()]
        self.ctx.sheets.update_field(r["id"], col, item.text())
        self.refresh()

    def load_sheet(self):
        f, _ = QFileDialog.getOpenFileName(self, "Cargar planilla", "", "Planillas (*.csv *.xlsx)")
        if not f:
            return
        names = [c.name for c in self.ctx.channels.list()]
        if not names:
            QMessageBox.information(self, "Canales", "Crea primero los canales en Ajustes.")
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getItem(self, "Canal", "¿A qué canal pertenece esta planilla?",
                                        ["(usar columna «canal»)"] + names, 0, False)
        if ok:
            self._load(f, None if name.startswith("(") else name)

    def _load(self, path: str, channel: str | None):
        def done(rep):
            msg = (f"{Path(path).name}: {rep.inserted} nuevas, {rep.updated} actualizadas, "
                   f"{rep.unchanged} sin cambios, {rep.error_count} con errores.")
            if rep.missing_columns:
                msg += "\nFaltan columnas: " + ", ".join(rep.missing_columns)
            bad = [f"Fila {r.index}: " + "; ".join(r.errors) for r in rep.results if r.errors]
            if bad:
                msg += "\n\n" + "\n".join(bad[:25])
            QMessageBox.information(self, "Planilla cargada", msg)
            self.refresh()

        self.tasks.start(f"Cargando {Path(path).name}", lambda p, c: self.ctx.sheets.load_source(path, channel),
                         on_done=done, on_error=lambda e: QMessageBox.critical(self, "Error", e[:1500]))

    def reload_all(self):
        for c in self.ctx.channels.list():
            if c.sheet_path and Path(c.sheet_path).is_file():
                self._load(c.sheet_path, c.name)

    def download_template(self):
        f, _ = QFileDialog.getSaveFileName(self, "Guardar plantilla", "plantilla_shortsfactory.csv",
                                           "CSV (*.csv);;Excel (*.xlsx)")
        if f:
            sh.write_template(f)

    def match_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de vídeos")
        if not d:
            return
        matched = unmatched_rows = 0
        rows, extra = sh.match_folder(self.rows, d)
        for old, new in zip(self.rows, rows):
            if new["archivo_video"] != old["archivo_video"]:
                self.ctx.sheets.update_field(old["id"], "archivo_video", new["archivo_video"])
                matched += 1
            elif not Path(old["archivo_video"]).is_file():
                unmatched_rows += 1
        QMessageBox.information(self, "Emparejar", f"Emparejadas {matched} filas. Filas sin vídeo: {unmatched_rows}.\n"
                                                   f"Vídeos sin fila: {', '.join(extra[:20]) or 'ninguno'}")
        self.refresh()

    def templates(self):
        TemplatesDialog(self.ctx, self).exec()

    def export(self):
        f, _ = QFileDialog.getSaveFileName(self, "Exportar planilla", "planilla_exportada.csv",
                                           "CSV (*.csv);;Excel (*.xlsx)")
        if f:
            self.ctx.sheets.export(f, self.f_channel.currentData())

    def enqueue(self):
        sel = sorted({i.row() for i in self.table.selectedIndexes()})
        n = skipped = 0
        for i in sel:
            r = self.rows[i]
            real = [e for e in json.loads(r["errors"] or "[]") if not e.startswith("aviso:")]
            if real or r["estado"] == Status.PUBLISHED.value:
                skipped += 1
                continue
            self.ctx.queue.add_from_row(r)
            n += 1
        QMessageBox.information(self, "Cola", f"Encoladas {n} filas. Omitidas {skipped} (con errores o publicadas).")
        if self.main:
            self.main.refresh_all()
