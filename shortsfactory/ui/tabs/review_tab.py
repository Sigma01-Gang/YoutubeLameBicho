"""Pestaña Revisión: vista previa en bucle, comparación, lista de comprobación, control técnico y aprobación."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from ...review.checks import run_checks
from ...sheets.sheets import preview_card

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAS_MM = True
except ImportError:  # pragma: no cover
    HAS_MM = False


class LoopPlayer(QWidget):
    def __init__(self, title: str):
        super().__init__()
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(QLabel(title))
        if HAS_MM:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setLoops(QMediaPlayer.Infinite)
            self.video = QVideoWidget()
            self.video.setMinimumSize(220, 390)
            self.player.setVideoOutput(self.video)
            l.addWidget(self.video, 1)
        else:
            self.player = None
            l.addWidget(QLabel("QtMultimedia no disponible"), 1)

    def play(self, path: str | None):
        if self.player and path:
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()

    def stop(self):
        if self.player:
            self.player.stop()


class ReviewTab(QWidget):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        self.current: dict | None = None
        lay = QHBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        left = QWidget()
        ll = QVBoxLayout(left)
        self.only_pending = QCheckBox("Solo esperando aprobación")
        self.only_pending.setChecked(True)
        self.only_pending.toggled.connect(self.refresh)
        ll.addWidget(self.only_pending)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda cur, _: self.select(cur))
        ll.addWidget(self.list)
        self.mode = QLabel()
        ll.addWidget(self.mode)
        split.addWidget(left)

        mid = QWidget()
        ml = QVBoxLayout(mid)
        players = QHBoxLayout()
        self.p1 = LoopPlayer("Versión actual (en bucle)")
        self.p2 = LoopPlayer("Comparar con…")
        players.addWidget(self.p1)
        players.addWidget(self.p2)
        ml.addLayout(players, 3)
        cmp_bar = QHBoxLayout()
        b_cmp = QPushButton("Elegir versión para comparar…")
        b_cmp.clicked.connect(self.pick_compare)
        b_stop = QPushButton("Detener")
        b_stop.clicked.connect(lambda: (self.p1.stop(), self.p2.stop()))
        cmp_bar.addWidget(b_cmp)
        cmp_bar.addWidget(b_stop)
        ml.addLayout(cmp_bar)
        self.meta = QTextEdit()
        self.meta.setReadOnly(True)
        ml.addWidget(self.meta, 1)
        split.addWidget(mid)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.check_box = QGroupBox("Lista de comprobación")
        self.check_lay = QVBoxLayout(self.check_box)
        rl.addWidget(self.check_box)
        qc_btn = QPushButton("Ejecutar comprobaciones técnicas")
        qc_btn.clicked.connect(self.run_qc)
        rl.addWidget(qc_btn)
        self.qc = QListWidget()
        rl.addWidget(self.qc, 1)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Nota de revisión (opcional)")
        rl.addWidget(self.note)
        btns = QHBoxLayout()
        ok = QPushButton("✔ Aprobar")
        ok.clicked.connect(self.approve)
        ko = QPushButton("✖ Rechazar")
        ko.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(ko)
        rl.addLayout(btns)
        rl.addWidget(QLabel("Registro de aprobaciones:"))
        self.approvals = QListWidget()
        rl.addWidget(self.approvals, 1)
        split.addWidget(right)
        split.setSizes([280, 600, 380])
        self.refresh()

    def refresh(self):
        self.mode.setText("Modo de aprobación: " + ("ACTIVO — nada se sube sin aprobar"
                                                   if self.ctx.db.get_setting("approval_mode", True)
                                                   else "desactivado (vídeos ya revisados)"))
        cur_id = self.current["id"] if self.current else None
        self.list.clear()
        items = self.ctx.queue.items(status="esperando_aprobacion") if self.only_pending.isChecked() \
            else [i for i in self.ctx.queue.items() if i["status"] != "publicado"]
        for it in items:
            li = QListWidgetItem(f"[{it['canal']}] {it['title'] or Path(it['video_path']).name} · v{it['version']}")
            li.setData(Qt.UserRole, it["id"])
            self.list.addItem(li)
            if it["id"] == cur_id:
                self.list.setCurrentItem(li)
        self.build_checklist()

    def build_checklist(self):
        while self.check_lay.count():
            w = self.check_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.checks = []
        for text in self.ctx.db.get_setting("review_checklist", []) or []:
            cb = QCheckBox(text)
            self.check_lay.addWidget(cb)
            self.checks.append(cb)

    def select(self, li):
        if li is None:
            return
        self.current = self.ctx.queue.get(li.data(Qt.UserRole))
        it = self.current
        if not it:
            return
        self.p1.play(it["video_path"])
        card = preview_card({"titulo": it["title"], "descripcion": it["description"], "etiquetas": it["tags"]})
        self.meta.setPlainText(f"{card}\n\nEtiquetas: {it['tags']}\nPublicación: {it['publish_at'] or 'al subir'}\n"
                               f"Archivo: {it['video_path']}\nVersión: {it['version']}")
        self.qc.clear()
        for cb in self.checks:
            cb.setChecked(False)
        self.approvals.clear()
        for a in self.ctx.queue.approvals(it["id"]):
            self.approvals.addItem(f"{a['approved_at']} · v{a['version']} · sha256 {a['sha256'][:16]}… {a['note']}")

    def pick_compare(self):
        f, _ = QFileDialog.getOpenFileName(self, "Versión para comparar", "", "Vídeos (*.mp4 *.mov *.mkv)")
        if f:
            self.p2.play(f)

    def run_qc(self):
        if not self.current:
            return
        it = self.current
        opts = self.ctx.db.get_setting("render_defaults", {}) or {}

        def done(rep):
            self.qc.clear()
            if rep.info:
                self.qc.addItem(f"{rep.info.width}x{rep.info.height} · {rep.info.duration:.2f}s · "
                                f"{rep.info.bitrate // 1000} kb/s · pico {rep.max_volume} dB · media {rep.mean_volume} dB")
            for i in rep.issues:
                self.qc.addItem(f"{'❌' if i.level == 'error' else '⚠'} {i.message}")
            if not rep.issues:
                self.qc.addItem("✔ Sin problemas técnicos")

        self.tasks.start("Comprobaciones técnicas", lambda p, c: run_checks(
            self.ctx.tools, it["video_path"], is_short=bool(it["is_short"]), width=opts.get("width", 1080),
            height=opts.get("height", 1920), freeze_tail=opts.get("freeze_last", 0)), on_done=done,
            on_error=lambda e: QMessageBox.critical(self, "Error", e[:1500]))

    def approve(self):
        if not self.current:
            return
        missing = [cb.text() for cb in self.checks if not cb.isChecked()]
        if missing and QMessageBox.question(self, "Lista incompleta",
                                            "Faltan puntos de la lista:\n- " + "\n- ".join(missing) +
                                            "\n\n¿Aprobar de todos modos?") != QMessageBox.Yes:
            return
        checklist = {cb.text(): cb.isChecked() for cb in self.checks}
        try:
            sha = self.ctx.queue.approve(self.current["id"], checklist, self.note.text())
        except OSError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        self.note.clear()
        QMessageBox.information(self, "Aprobado", f"Aprobado. SHA-256: {sha[:20]}…\n"
                                                  "Si el archivo cambia, la subida se bloqueará.")
        self.refresh()
        if self.main:
            self.main.queue_tab.refresh()

    def reject(self):
        if self.current:
            self.ctx.queue.reject(self.current["id"], self.note.text())
            self.note.clear()
            self.refresh()
