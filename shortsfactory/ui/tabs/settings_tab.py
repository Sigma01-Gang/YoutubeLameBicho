"""Pestaña Ajustes: FFmpeg, comportamiento, canales, atajos, glosario, copias y proyectos."""
from __future__ import annotations

import json

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...ffmpeg import runner
from ...upload.studio import AUTOMATION_NOTICE, StudioUploader
from ..workers import run_bg

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


class ChannelEditor(QGroupBox):
    def __init__(self, ctx, on_change):
        super().__init__("Canales (perfiles aislados)")
        self.ctx, self.on_change = ctx, on_change
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.load)
        left.addWidget(self.list)
        for text, fn in (("Nuevo canal…", self.new), ("Clonar para otro idioma…", self.clone),
                         ("Eliminar", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            left.addWidget(b)
        lay.addLayout(left, 1)
        form = QFormLayout()
        self.name = QLineEdit()
        self.lang = QComboBox()
        self.lang.addItems(["es", "en"])
        self.studio_lang = QComboBox()
        self.studio_lang.addItems(["es", "en"])
        self.folder = QLineEdit()
        self.slots = QLineEdit()
        self.slots.setPlaceholderText("09:00-12:00, 18:00-23:00")
        self.gap = QSpinBox()
        self.gap.setRange(0, 10000)
        self.gap.setSuffix(" min")
        self.limit = QSpinBox()
        self.limit.setRange(0, 100)
        self.limit.setSpecialValueText("sin límite")
        self.rest = QHBoxLayout()
        self.rest_boxes = [QCheckBox(d) for d in DAYS]
        for cb in self.rest_boxes:
            self.rest.addWidget(cb)
        self.playlist = QLineEdit()
        self.playlist.setPlaceholderText("Nombre exacto de una lista existente")
        for lab, w in (("Nombre", self.name), ("Idioma", self.lang), ("Idioma de Studio", self.studio_lang),
                       ("Carpeta", self.folder), ("Franjas preferidas", self.slots), ("Separación mínima", self.gap),
                       ("Límite diario", self.limit), ("Lista de reproducción", self.playlist)):
            form.addRow(lab, w)
        form.addRow("Días de descanso", self.rest)
        btns = QHBoxLayout()
        for text, fn in (("Guardar canal", self.save), ("Iniciar sesión en Studio (manual)", self.login),
                         ("Comprobar sesión", self.check_session)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        form.addRow(btns)
        lay.addLayout(form, 3)
        self.refresh()

    def channels(self):
        return self.ctx.channels.list()

    def refresh(self):
        self.list.clear()
        for c in self.channels():
            self.list.addItem(f"{c.name} ({c.language}){' — en pausa' if c.paused else ''}")
        if self.list.count():
            self.list.setCurrentRow(0)

    def current(self):
        i = self.list.currentRow()
        chans = self.channels()
        return chans[i] if 0 <= i < len(chans) else None

    def load(self, _=None):
        c = self.current()
        if not c:
            return
        self.name.setText(c.name)
        self.lang.setCurrentText(c.language)
        self.studio_lang.setCurrentText(c.studio_lang)
        self.folder.setText(c.folder)
        self.slots.setText(", ".join(c.preferred_slots))
        self.gap.setValue(c.min_gap_minutes)
        self.limit.setValue(c.daily_limit)
        for i, cb in enumerate(self.rest_boxes):
            cb.setChecked(i in c.rest_days)
        self.playlist.setText(c.playlist)

    def save(self):
        c = self.current()
        if not c:
            return
        from ...scheduling.scheduler import parse_slot
        slots = [s.strip() for s in self.slots.text().split(",") if s.strip()]
        try:
            for s in slots:
                parse_slot(s)
        except ValueError:
            QMessageBox.warning(self, "Franjas", "Formato de franja no válido. Usa HH:MM-HH:MM.")
            return
        c.name, c.language, c.studio_lang = self.name.text().strip(), self.lang.currentText(), self.studio_lang.currentText()
        c.folder, c.preferred_slots = self.folder.text().strip(), slots
        c.min_gap_minutes, c.daily_limit = self.gap.value(), self.limit.value()
        c.rest_days = [i for i, cb in enumerate(self.rest_boxes) if cb.isChecked()]
        c.playlist = self.playlist.text().strip()
        self.ctx.channels.update(c)
        self.refresh()
        self.on_change()

    def new(self):
        name, ok = QInputDialog.getText(self, "Nuevo canal", "Nombre:")
        if ok and name.strip():
            lang, ok2 = QInputDialog.getItem(self, "Idioma", "Idioma:", ["es", "en"], 0, False)
            if ok2:
                self.ctx.channels.create(name.strip(), lang)
                self.refresh()
                self.on_change()

    def clone(self):
        c = self.current()
        if not c:
            return
        name, ok = QInputDialog.getText(self, "Clonar", "Nombre del nuevo canal:", text=f"{c.name} EN")
        if ok and name.strip():
            lang, ok2 = QInputDialog.getItem(self, "Idioma", "Idioma:", ["en", "es"], 0, False)
            if ok2:
                self.ctx.channels.clone(c.id, name.strip(), lang)
                self.refresh()
                self.on_change()

    def delete(self):
        c = self.current()
        if c and QMessageBox.question(self, "Eliminar", f"¿Eliminar «{c.name}» y su cola?") == QMessageBox.Yes:
            self.ctx.channels.delete(c.id)
            self.refresh()
            self.on_change()

    def login(self):
        c = self.current()
        if not c:
            return
        QMessageBox.information(self, "Inicio de sesión manual",
                                "Se abrirá Chromium con el perfil de este canal.\nInicia sesión en YouTube a mano y "
                                "cierra la ventana al terminar. La sesión queda guardada en el perfil (no se guardan "
                                "contraseñas).\n\n" + AUTOMATION_NOTICE)
        up = StudioUploader(c.profile_dir, c.studio_lang)

        def after(_):
            self.ctx.channels.set_paused(c.id, False)
            self.refresh()
            self.on_change()

        run_bg(up.open_login, on_done=after, on_error=lambda e: QMessageBox.critical(self, "Error", e[:1500]))

    def check_session(self):
        c = self.current()
        if not c:
            return
        up = StudioUploader(c.profile_dir, c.studio_lang, headless=True)
        run_bg(up.check_session,
               on_done=lambda ok: QMessageBox.information(self, "Sesión", "Sesión activa." if ok else
                                                          "La sesión ha caducado: inicia sesión a mano."),
               on_error=lambda e: QMessageBox.critical(self, "Error", e[:1500]))


class SettingsTab(QScrollArea):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        db = ctx.db

        ff = QGroupBox("FFmpeg")
        f1 = QFormLayout(ff)
        self.ffmpeg = QLineEdit(db.get_setting("ffmpeg_path"))
        self.ffprobe = QLineEdit(db.get_setting("ffprobe_path"))
        test = QPushButton("Comprobar FFmpeg")
        test.clicked.connect(self.test_ffmpeg)
        pick = QPushButton("Buscar ffmpeg…")
        pick.clicked.connect(self.pick_ffmpeg)
        self.hw = QComboBox()
        for lab, v in (("Automática (con respaldo en CPU)", "auto"), ("Solo CPU (libx264)", "none"),
                       ("NVIDIA NVENC", "nvenc"), ("Intel QuickSync", "qsv"), ("AMD AMF", "amf")):
            self.hw.addItem(lab, v)
        self.hw.setCurrentIndex(max(0, self.hw.findData(db.get_setting("hwaccel"))))
        self.threads = QSpinBox()
        self.threads.setRange(1, 16)
        self.threads.setValue(int(db.get_setting("threads")))
        self.cache = QCheckBox("Caché por hash (no recodificar vídeos sin cambios)")
        self.cache.setChecked(bool(db.get_setting("cache_enabled")))
        f1.addRow("ffmpeg", self.ffmpeg)
        f1.addRow("ffprobe", self.ffprobe)
        row = QHBoxLayout()
        row.addWidget(pick)
        row.addWidget(test)
        f1.addRow(row)
        f1.addRow("Aceleración por hardware", self.hw)
        f1.addRow("Hilos de procesamiento en paralelo", self.threads)
        f1.addRow(self.cache)
        lay.addWidget(ff)

        cut = QGroupBox("Cortes")
        f2 = QFormLayout(cut)
        self.part = QSpinBox()
        self.part.setRange(10, 3600)
        self.part.setValue(int(db.get_setting("part_seconds")))
        self.clean = QCheckBox("Cortes limpios en el silencio más cercano")
        self.clean.setChecked(bool(db.get_setting("clean_cuts")))
        self.tol = self._d(1, 60, db.get_setting("clean_cut_tolerance"))
        self.sil_db = self._d(-80, -10, db.get_setting("silence_db"))
        self.sil_min = self._d(0.1, 5, db.get_setting("silence_min"))
        self.pause = self._d(0.3, 10, db.get_setting("pause_threshold"))
        self.scene = self._d(0.05, 1, db.get_setting("scene_threshold"))
        for lab, w in (("Duración de parte (s)", self.part), ("", self.clean), ("Tolerancia corte limpio (s)", self.tol),
                       ("Umbral de silencio (dB)", self.sil_db), ("Silencio mínimo (s)", self.sil_min),
                       ("Pausa larga a eliminar (s)", self.pause), ("Umbral cambio de plano", self.scene)):
            f2.addRow(lab, w)
        lay.addWidget(cut)

        beh = QGroupBox("Cola, revisión y subida")
        f3 = QFormLayout(beh)
        self.approval = QCheckBox("Modo de aprobación (ningún vídeo se sube sin aprobar)")
        self.approval.setChecked(bool(db.get_setting("approval_mode")))
        self.retries = QSpinBox()
        self.retries.setRange(1, 10)
        self.retries.setValue(int(db.get_setting("max_retries")))
        self.retry_base = self._d(1, 240, db.get_setting("retry_base_minutes"))
        self.test_mode = QCheckBox("Modo de prueba (subir como privado/no listado)")
        self.test_mode.setChecked(bool(db.get_setting("test_mode")))
        self.test_vis = QComboBox()
        self.test_vis.addItems(["PRIVATE", "UNLISTED"])
        self.test_vis.setCurrentText(db.get_setting("test_visibility"))
        self.sim = QCheckBox("Modo simulación (la cola no sube nada)")
        self.sim.setChecked(bool(db.get_setting("simulation_mode")))
        self.checklist = QPlainTextEdit("\n".join(db.get_setting("review_checklist") or []))
        self.checklist.setMaximumHeight(110)
        for lab, w in (("", self.approval), ("Reintentos máximos", self.retries),
                       ("Espera base reintento (min)", self.retry_base), ("", self.test_mode),
                       ("Visibilidad de prueba", self.test_vis), ("", self.sim),
                       ("Lista de comprobación (una por línea)", self.checklist)):
            f3.addRow(lab, w)
        notice = QLabel("⚠ " + AUTOMATION_NOTICE)
        notice.setWordWrap(True)
        f3.addRow(notice)
        lay.addWidget(beh)

        self.channel_editor = ChannelEditor(ctx, self.on_channels_changed)
        lay.addWidget(self.channel_editor)

        misc = QGroupBox("Interfaz, atajos y glosario")
        f4 = QFormLayout(misc)
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(db.get_setting("theme"))
        self.shortcuts = QPlainTextEdit(json.dumps(db.get_setting("shortcuts"), indent=1, ensure_ascii=False))
        self.shortcuts.setMaximumHeight(140)
        self.glossary = QPlainTextEdit("\n".join(db.get_setting("glossary") or []))
        self.glossary.setMaximumHeight(100)
        f4.addRow("Tema", self.theme)
        f4.addRow("Atajos (JSON, requieren reinicio)", self.shortcuts)
        f4.addRow("Glosario de nombres propios", self.glossary)
        lay.addWidget(misc)

        data = QGroupBox("Datos")
        f5 = QHBoxLayout(data)
        self.keep = QSpinBox()
        self.keep.setRange(1, 100)
        self.keep.setValue(int(db.get_setting("backup_keep")))
        for text, fn in (("Copia de seguridad ahora", self.backup), ("Exportar proyecto JSON…", self.export_project),
                         ("Importar proyecto JSON…", self.import_project)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            f5.addWidget(b)
        f5.addWidget(QLabel("Copias a conservar:"))
        f5.addWidget(self.keep)
        lay.addWidget(data)

        save = QPushButton("Guardar ajustes")
        save.clicked.connect(self.save)
        lay.addWidget(save)
        lay.addStretch()

    @staticmethod
    def _d(lo, hi, v):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(2)
        w.setValue(float(v))
        return w

    def on_channels_changed(self):
        if self.main:
            self.main.refresh_all()

    def pick_ffmpeg(self):
        f, _ = QFileDialog.getOpenFileName(self, "Ejecutable de ffmpeg")
        if f:
            self.ffmpeg.setText(f)
            guess = f.replace("ffmpeg", "ffprobe")
            self.ffprobe.setText(guess)

    def test_ffmpeg(self):
        tools = runner.Tools(self.ffmpeg.text().strip(), self.ffprobe.text().strip())
        ver = runner.ffmpeg_version(tools)
        if not ver:
            QMessageBox.critical(self, "FFmpeg", "No se pudo ejecutar ffmpeg. Revisa la ruta.")
            return
        encs = runner.available_encoders(tools)
        hw = [e for e in ("h264_nvenc", "h264_qsv", "h264_amf") if e in encs]
        filters = runner.available_filters(tools)
        missing = [f for f in ("ass", "drawtext", "vidstabdetect", "sidechaincompress", "loudnorm", "colortemperature")
                   if f not in filters]
        QMessageBox.information(self, "FFmpeg", f"{ver}\nCodificadores hardware compilados: {', '.join(hw) or 'ninguno'}"
                                                f"\nFiltros que faltan: {', '.join(missing) or 'ninguno'}")

    def save(self):
        db = self.ctx.db
        try:
            shortcuts = json.loads(self.shortcuts.toPlainText())
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Atajos", f"JSON no válido: {exc}")
            return
        values = {
            "ffmpeg_path": self.ffmpeg.text().strip() or "ffmpeg", "ffprobe_path": self.ffprobe.text().strip() or "ffprobe",
            "hwaccel": self.hw.currentData(), "threads": self.threads.value(), "cache_enabled": self.cache.isChecked(),
            "part_seconds": self.part.value(), "clean_cuts": self.clean.isChecked(),
            "clean_cut_tolerance": self.tol.value(), "silence_db": self.sil_db.value(),
            "silence_min": self.sil_min.value(), "pause_threshold": self.pause.value(),
            "scene_threshold": self.scene.value(), "approval_mode": self.approval.isChecked(),
            "max_retries": self.retries.value(), "retry_base_minutes": self.retry_base.value(),
            "test_mode": self.test_mode.isChecked(), "test_visibility": self.test_vis.currentText(),
            "simulation_mode": self.sim.isChecked(),
            "review_checklist": [l.strip() for l in self.checklist.toPlainText().splitlines() if l.strip()],
            "theme": self.theme.currentText(), "shortcuts": shortcuts,
            "glossary": [l.strip() for l in self.glossary.toPlainText().splitlines() if l.strip()],
            "backup_keep": self.keep.value(),
        }
        for k, v in values.items():
            db.set_setting(k, v)
        if self.main:
            self.main.apply_theme()
            self.main.refresh_all()
        QMessageBox.information(self, "Ajustes", "Ajustes guardados.")

    def backup(self):
        p = self.ctx.backup()
        QMessageBox.information(self, "Copia de seguridad", f"Copia creada en:\n{p}")

    def export_project(self):
        f, _ = QFileDialog.getSaveFileName(self, "Exportar proyecto", "proyecto.json", "JSON (*.json)")
        if f:
            proj = self.main.edit_tab.project_data() if self.main else {}
            self.ctx.export_project(f, proj)

    def import_project(self):
        f, _ = QFileDialog.getOpenFileName(self, "Importar proyecto", "", "JSON (*.json)")
        if f:
            proj = self.ctx.import_project(f)
            if proj.get("source"):
                self.ctx.db.set_setting(f"project:{proj['source']}", {"segments": proj.get("segments", []),
                                                                       "options": proj.get("options", {})})
                srcs = self.ctx.db.get_setting("sources", []) or []
                if proj["source"] not in srcs:
                    self.ctx.db.set_setting("sources", srcs + [proj["source"]])
            if self.main:
                self.main.refresh_all()
            QMessageBox.information(self, "Proyecto", "Proyecto importado.")
