"""Asistente de primer uso: FFmpeg, perfiles de canal y carpetas."""
from __future__ import annotations

import shutil

from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWizard, QWizardPage)

from ..config import sub_dir
from ..ffmpeg import runner
from ..upload.studio import AUTOMATION_NOTICE


class FirstRunWizard(QWizard):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Asistente de primer uso")
        self.resize(640, 420)

        # 1. FFmpeg
        p1 = QWizardPage()
        p1.setTitle("1. FFmpeg")
        p1.setSubTitle("Todo el procesamiento de vídeo es local con FFmpeg/ffprobe.")
        f1 = QFormLayout(p1)
        self.ffmpeg = QLineEdit(shutil.which("ffmpeg") or ctx.db.get_setting("ffmpeg_path"))
        self.ffprobe = QLineEdit(shutil.which("ffprobe") or ctx.db.get_setting("ffprobe_path"))
        pick = QPushButton("Buscar…")
        pick.clicked.connect(self._pick)
        test = QPushButton("Comprobar")
        self.ff_status = QLabel()
        test.clicked.connect(self._test)
        f1.addRow("ffmpeg", self.ffmpeg)
        f1.addRow("ffprobe", self.ffprobe)
        row = QHBoxLayout()
        row.addWidget(pick)
        row.addWidget(test)
        f1.addRow(row)
        f1.addRow(self.ff_status)
        self.addPage(p1)

        # 2. Canales
        p2 = QWizardPage()
        p2.setTitle("2. Canales")
        p2.setSubTitle("Cada canal tiene su carpeta, su sesión de navegador, sus plantillas y su planilla.")
        f2 = QFormLayout(p2)
        self.es_name = QLineEdit("Español")
        self.en_name = QLineEdit("Inglés")
        self.make_en = QCheckBox("Crear también el canal en inglés (clonando ajustes)")
        self.make_en.setChecked(True)
        f2.addRow("Canal en español", self.es_name)
        f2.addRow("Canal en inglés", self.en_name)
        f2.addRow(self.make_en)
        note = QLabel("Después, en Ajustes › Canales, pulsa «Iniciar sesión en Studio» para cada canal e inicia "
                      "sesión a mano una vez. No se guardan contraseñas.\n\n" + AUTOMATION_NOTICE)
        note.setWordWrap(True)
        f2.addRow(note)
        self.addPage(p2)

        # 3. Carpetas
        p3 = QWizardPage()
        p3.setTitle("3. Carpetas")
        l3 = QVBoxLayout(p3)
        f3 = QFormLayout()
        self.out = QLineEdit(ctx.db.get_setting("output_dir") or str(sub_dir("exports")))
        b = QPushButton("Elegir…")
        b.clicked.connect(lambda: self.out.setText(QFileDialog.getExistingDirectory(self, "Salida") or self.out.text()))
        f3.addRow("Carpeta de exportación", self.out)
        f3.addRow(b)
        l3.addLayout(f3)
        l3.addWidget(QLabel("Los datos de la app (base de datos, perfiles, copias) se guardan en:\n"
                            + str(sub_dir(".."))))
        self.addPage(p3)

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "ffmpeg")
        if f:
            self.ffmpeg.setText(f)
            self.ffprobe.setText(f.replace("ffmpeg", "ffprobe"))

    def _test(self):
        ver = runner.ffmpeg_version(runner.Tools(self.ffmpeg.text(), self.ffprobe.text()))
        self.ff_status.setText(ver or "❌ No se pudo ejecutar ffmpeg")

    def accept(self):
        db = self.ctx.db
        db.set_setting("ffmpeg_path", self.ffmpeg.text().strip() or "ffmpeg")
        db.set_setting("ffprobe_path", self.ffprobe.text().strip() or "ffprobe")
        db.set_setting("output_dir", self.out.text().strip())
        if not self.ctx.channels.list():
            es = self.ctx.channels.create(self.es_name.text().strip() or "Español", "es")
            if self.make_en.isChecked():
                self.ctx.channels.clone(es.id, self.en_name.text().strip() or "Inglés", "en")
        db.set_setting("first_run_done", True)
        super().accept()
