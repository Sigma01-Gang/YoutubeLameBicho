"""Pestaña Vídeos: vídeos de origen, integridad, modo de un clic y biblioteca de activos."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)
from PySide6.QtCore import Qt

from ... import pipeline
from ...core.timeutil import format_short
from ...ffmpeg import runner
from ...library.files import check_source_integrity


class VideosTab(QWidget):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Vertical)
        lay.addWidget(split)

        src_box = QGroupBox("Vídeos de origen")
        sl = QVBoxLayout(src_box)
        bar = QHBoxLayout()
        for text, fn in (("Importar vídeos…", self.import_videos), ("Quitar de la lista", self.remove),
                         ("Comprobar integridad", self.check), ("Abrir en Edición", self.open_edit)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        sl.addLayout(bar)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Archivo", "Duración", "Resolución", "FPS", "Estado"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.doubleClicked.connect(lambda _: self.open_edit())
        sl.addWidget(self.table)

        oc = QHBoxLayout()
        self.oc_channel = QComboBox()
        self.oc_mode = QComboBox()
        self.oc_mode.addItem("Partes (historias por partes)", "parts")
        self.oc_mode.addItem("Shorts (15–60 s)", "shorts")
        b_oc = QPushButton("⚡ Modo de un clic: procesar y encolar")
        b_oc.clicked.connect(self.one_click)
        oc.addWidget(QLabel("Canal:"))
        oc.addWidget(self.oc_channel)
        oc.addWidget(QLabel("Tipo:"))
        oc.addWidget(self.oc_mode)
        oc.addWidget(b_oc)
        oc.addStretch()
        sl.addLayout(oc)
        split.addWidget(src_box)

        lib_box = QGroupBox("Biblioteca de activos (fondos, música, overlays, efectos, fuentes)")
        ll = QVBoxLayout(lib_box)
        lbar = QHBoxLayout()
        self.filter_kind = QComboBox()
        self.filter_kind.addItems(["(todos)", "fondo", "musica", "sfx", "overlay", "fuente", "otro"])
        self.filter_kind.currentIndexChanged.connect(self.refresh_assets)
        self.filter_tag = QLineEdit()
        self.filter_tag.setPlaceholderText("filtrar por etiqueta")
        self.filter_tag.textChanged.connect(self.refresh_assets)
        for text, fn in (("Añadir archivos…", self.add_assets), ("Añadir carpeta…", self.add_folder),
                         ("Etiquetar…", self.tag_assets), ("Quitar", self.remove_assets)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            lbar.addWidget(b)
        lbar.addWidget(self.filter_kind)
        lbar.addWidget(self.filter_tag)
        ll.addLayout(lbar)
        self.assets = QTableWidget(0, 3)
        self.assets.setHorizontalHeaderLabels(["Tipo", "Etiquetas", "Ruta"])
        self.assets.horizontalHeader().setStretchLastSection(True)
        self.assets.setSelectionBehavior(QTableWidget.SelectRows)
        ll.addWidget(self.assets)
        split.addWidget(lib_box)
        self.refresh()

    # --- origen -------------------------------------------------------------
    def sources(self) -> list[str]:
        return self.ctx.db.get_setting("sources", []) or []

    def refresh(self):
        srcs = self.sources()
        self.table.setRowCount(len(srcs))
        meta = self.ctx.db.get_setting("source_meta", {}) or {}
        for i, p in enumerate(srcs):
            m = meta.get(p, {})
            vals = [p, format_short(m.get("duration", 0)) if m else "", m.get("res", ""), m.get("fps", ""),
                    m.get("status", "" if Path(p).is_file() else "no encontrado")]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))
        self.oc_channel.clear()
        for c in self.ctx.channels.list():
            self.oc_channel.addItem(f"{c.name} ({c.language})", c.id)
        self.refresh_assets()

    def import_videos(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Importar vídeos", "", "Vídeos (*.mp4 *.mov *.mkv *.webm *.avi)")
        if not files:
            return
        srcs = self.sources()
        srcs += [f for f in files if f not in srcs]
        self.ctx.db.set_setting("sources", srcs)
        self._probe(files)

    def _probe(self, files):
        tools = self.ctx.tools

        def work(progress, cancel):
            out = {}
            for i, f in enumerate(files):
                ok, why = check_source_integrity(tools, f)
                m = {"status": "ok" if ok else why}
                if ok:
                    info = runner.media_info(tools, f)
                    m.update(duration=info.duration, res=f"{info.width}x{info.height}", fps=f"{info.fps:.2f}")
                out[f] = m
                progress((i + 1) / len(files))
            return out

        def done(res):
            meta = self.ctx.db.get_setting("source_meta", {}) or {}
            meta.update(res)
            self.ctx.db.set_setting("source_meta", meta)
            self.refresh()
            if self.main:
                self.main.edit_tab.refresh_sources()

        self.tasks.start("Analizando vídeos importados", work, on_done=done)

    def selected(self) -> list[str]:
        srcs = self.sources()
        return [srcs[i] for i in sorted({ix.row() for ix in self.table.selectedIndexes()}) if i < len(srcs)]

    def remove(self):
        sel = set(self.selected())
        self.ctx.db.set_setting("sources", [s for s in self.sources() if s not in sel])
        self.refresh()

    def check(self):
        self._probe(self.selected() or self.sources())

    def open_edit(self):
        sel = self.selected()
        if sel and self.main:
            self.main.edit_tab.refresh_sources()
            et = self.main.edit_tab
            et.source.setCurrentIndex(max(0, et.source.findData(sel[0])))
            et.load(sel[0])
            self.main.tabs.setCurrentWidget(et)

    def one_click(self):
        sel = self.selected()
        if not sel:
            QMessageBox.information(self, "Un clic", "Selecciona uno o más vídeos de origen.")
            return
        ch = self.oc_channel.currentData()
        mode = self.oc_mode.currentData()
        for src in sel:
            self.tasks.start(f"Un clic: {Path(src).name}", lambda p, c, s=src: pipeline.one_click(self.ctx, s, ch, mode, p, c),
                             on_done=self._oc_done, on_error=lambda e: QMessageBox.critical(self, "Error", e[:1500]))

    def _oc_done(self, res: dict):
        qc = [f"{Path(k).name}: {'; '.join(v)}" for k, v in res["qc"].items() if v]
        msg = (f"Segmentos: {res['segments']}\nRenderizados: {res['rendered']}\nEncolados: {len(res['queued'])}"
               + ("\n\nErrores:\n" + "\n".join(res["errors"]) if res["errors"] else "")
               + ("\n\nComprobaciones:\n" + "\n".join(qc[:15]) if qc else ""))
        QMessageBox.information(self, "Modo de un clic terminado", msg)
        if self.main:
            self.main.refresh_all()

    # --- biblioteca -------------------------------------------------------------
    def refresh_assets(self):
        kind = self.filter_kind.currentText()
        rows = self.ctx.assets.list(None if kind == "(todos)" else kind, self.filter_tag.text().strip() or None)
        self.assets.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for c, k in enumerate(("kind", "tags", "path")):
                self.assets.setItem(i, c, QTableWidgetItem(r[k]))

    def add_assets(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir activos")
        for f in files:
            self.ctx.assets.add(f)
        self.refresh_assets()

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de activos")
        if d:
            n = self.ctx.assets.add_folder(d)
            QMessageBox.information(self, "Biblioteca", f"Añadidos {n} archivos.")
            self.refresh_assets()

    def _sel_assets(self) -> list[str]:
        return [self.assets.item(r, 2).text() for r in sorted({i.row() for i in self.assets.selectedIndexes()})]

    def tag_assets(self):
        paths = self._sel_assets()
        if not paths:
            return
        tags, ok = QInputDialog.getText(self, "Etiquetas", "Etiquetas separadas por comas:")
        if ok:
            for p in paths:
                kind = next((r["kind"] for r in self.ctx.assets.list() if r["path"] == p), None)
                self.ctx.assets.add(p, kind, [t.strip() for t in tags.split(",") if t.strip()])
            self.refresh_assets()

    def remove_assets(self):
        for p in self._sel_assets():
            self.ctx.assets.remove(p)
        self.refresh_assets()
