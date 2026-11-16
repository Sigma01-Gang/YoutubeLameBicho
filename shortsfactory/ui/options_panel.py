"""Panel de opciones de render (formato, encuadre, color, audio, textos)."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QTabWidget, QWidget)

from ..core.models import FORMAT_TEMPLATES, RenderOptions, SfxCue, SubtitleStyle

FRAMING = [("Fondo difuminado", "blur"), ("Recorte centrado", "crop"), ("Seguimiento de cara", "face"),
           ("Encuadre manual", "manual"), ("Letterbox (color/imagen)", "letterbox")]


def _dspin(lo, hi, val, step=0.1, dec=2):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(val)
    return w


def _spin(lo, hi, val):
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(val)
    return w


class PathEdit(QWidget):
    def __init__(self, filt: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        b = QPushButton("…")
        b.setFixedWidth(28)
        b.clicked.connect(self._pick)
        clr = QPushButton("✕")
        clr.setFixedWidth(28)
        clr.clicked.connect(lambda: self.edit.setText(""))
        lay.addWidget(self.edit)
        lay.addWidget(b)
        lay.addWidget(clr)
        self.filt = filt

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "Elegir archivo", "", self.filt)
        if f:
            self.edit.setText(f)

    def value(self) -> str | None:
        return self.edit.text().strip() or None

    def set(self, v: str | None):
        self.edit.setText(v or "")


class ColorButton(QPushButton):
    def __init__(self, color="#FFFFFF"):
        super().__init__()
        self.set(color)
        self.clicked.connect(self._pick)

    def _pick(self):
        from PySide6.QtGui import QColor
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.set(c.name().upper())

    def set(self, color: str):
        self.color = color
        self.setText(color)
        self.setStyleSheet(f"background:{color}; color:#000;")


class OptionsPanel(QTabWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # --- Formato y encuadre
        f1 = QWidget()
        l1 = QFormLayout(f1)
        self.template = QComboBox()
        self.template.addItems(list(FORMAT_TEMPLATES))
        self.framing = QComboBox()
        for label, val in FRAMING:
            self.framing.addItem(label, val)
        self.manual_x = _dspin(0, 1, 0.5, 0.05)
        self.manual_y = _dspin(0, 1, 0.5, 0.05)
        self.blur = _spin(1, 60, 20)
        self.lb_color = ColorButton("#000000")
        self.lb_image = PathEdit("Imágenes (*.png *.jpg *.jpeg *.webp)")
        self.frame_png = PathEdit("PNG (*.png)")
        self.kenburns = QCheckBox("Zoom gradual (Ken Burns)")
        self.kb_start = _dspin(1, 3, 1.0, 0.05)
        self.kb_end = _dspin(1, 3, 1.15, 0.05)
        self.kb_dur = _dspin(0, 600, 0, 1, 1)
        self.fps = _spin(24, 60, 30)
        self.crf = _spin(12, 35, 20)
        self.preset = QComboBox()
        self.preset.addItems(["ultrafast", "veryfast", "faster", "fast", "medium", "slow"])
        self.preset.setCurrentText("medium")
        for lab, w in (("Plantilla de formato", self.template), ("Encuadre", self.framing),
                       ("Posición X (manual)", self.manual_x), ("Posición Y (manual)", self.manual_y),
                       ("Intensidad difuminado", self.blur), ("Color letterbox", self.lb_color),
                       ("Imagen letterbox", self.lb_image), ("Marco PNG", self.frame_png), ("", self.kenburns),
                       ("Zoom inicial", self.kb_start), ("Zoom final", self.kb_end),
                       ("Duración zoom (0 = todo)", self.kb_dur), ("FPS", self.fps),
                       ("Calidad (CRF, menor = mejor)", self.crf), ("Preset", self.preset)):
            l1.addRow(lab, w)
        self.addTab(f1, "Formato")

        # --- Color y efectos
        f2 = QWidget()
        l2 = QFormLayout(f2)
        self.brightness = _dspin(-1, 1, 0, 0.02)
        self.contrast = _dspin(0, 3, 1, 0.05)
        self.saturation = _dspin(0, 3, 1, 0.05)
        self.temperature = _spin(1000, 40000, 6500)
        self.stabilize = QCheckBox("Estabilizar (vidstab)")
        self.autocrop = QCheckBox("Quitar barras negras automáticamente")
        self.fade_in = _dspin(0, 10, 0.3)
        self.fade_out = _dspin(0, 10, 0.3)
        self.freeze = _dspin(0, 10, 1.5)
        for lab, w in (("Brillo", self.brightness), ("Contraste", self.contrast), ("Saturación", self.saturation),
                       ("Temperatura (K)", self.temperature), ("", self.stabilize), ("", self.autocrop),
                       ("Fundido entrada (s)", self.fade_in), ("Fundido salida (s)", self.fade_out),
                       ("Congelar último fotograma (s)", self.freeze)):
            l2.addRow(lab, w)
        self.addTab(f2, "Color y efectos")

        # --- Audio
        f3 = QWidget()
        l3 = QFormLayout(f3)
        self.loudnorm = QCheckBox("Normalizar sonoridad (EBU R128)")
        self.loudnorm.setChecked(True)
        self.lufs = _dspin(-30, -5, -14, 0.5, 1)
        self.music = PathEdit("Audio (*.mp3 *.wav *.m4a *.ogg *.flac)")
        self.music_vol = _dspin(0, 2, 0.15, 0.05)
        self.ducking = QCheckBox("Ducking automático bajo la voz")
        self.ducking.setChecked(True)
        self.voice = PathEdit("Audio (*.mp3 *.wav *.m4a *.ogg *.flac)")
        self.voice_mode = QComboBox()
        self.voice_mode.addItem("Mezclar con el original", "mix")
        self.voice_mode.addItem("Sustituir el audio original", "replace")
        self.voice_offset = _dspin(0, 600, 0, 0.1)
        self.sfx = QLineEdit()
        self.sfx.setPlaceholderText("ruta|segundo|volumen; ruta2|segundo|volumen")
        for lab, w in (("", self.loudnorm), ("Objetivo LUFS", self.lufs), ("Música de fondo", self.music),
                       ("Volumen música", self.music_vol), ("", self.ducking), ("Voz grabada", self.voice),
                       ("Modo voz", self.voice_mode), ("Desfase voz (s)", self.voice_offset),
                       ("Efectos de sonido", self.sfx)):
            l3.addRow(lab, w)
        self.addTab(f3, "Audio")

        # --- Textos
        f4 = QWidget()
        l4 = QFormLayout(f4)
        self.subs = PathEdit("Subtítulos (*.srt *.vtt)")
        self.words = PathEdit("CSV (*.csv)")
        self.sub_font = QLineEdit("Arial")
        self.sub_size = _spin(10, 200, 64)
        self.sub_color = ColorButton("#FFFFFF")
        self.sub_outline_color = ColorButton("#000000")
        self.sub_outline = _spin(0, 20, 4)
        self.sub_pos = QComboBox()
        for lab, v in (("Abajo", "bottom"), ("Centro", "middle"), ("Arriba", "top")):
            self.sub_pos.addItem(lab, v)
        self.sub_margin = _spin(0, 1500, 260)
        self.sub_anim = QComboBox()
        for lab, v in (("Ninguna", "none"), ("Fundido", "fade"), ("Deslizamiento", "slide")):
            self.sub_anim.addItem(lab, v)
        self.highlight = ColorButton("#FFD400")
        self.part_numbers = QCheckBox("Numerar partes («Parte 1/5»)")
        self.part_numbers.setChecked(True)
        self.progress = QCheckBox("Barra de progreso de la historia")
        self.progress.setChecked(True)
        self.progress_color = ColorButton("#FF3B30")
        self.signature = QLineEdit()
        self.logo = PathEdit("Imágenes (*.png)")
        self.sig_pos = QComboBox()
        self.sig_pos.addItems(["top-right", "top-left", "bottom-right", "bottom-left", "center"])
        self.sig_start = _dspin(0, 3600, 0, 0.5)
        self.sig_dur = _dspin(0, 3600, 0, 0.5)
        self.text_anim = QComboBox()
        for lab, v in (("Fundido", "fade"), ("Deslizamiento", "slide"), ("Ninguna", "none")):
            self.text_anim.addItem(lab, v)
        self.font_file = PathEdit("Fuentes (*.ttf *.otf)")
        for lab, w in (("Subtítulos (SRT/VTT)", self.subs), ("Tiempos por palabra (karaoke)", self.words),
                       ("Fuente", self.sub_font), ("Tamaño", self.sub_size), ("Color", self.sub_color),
                       ("Color contorno", self.sub_outline_color), ("Contorno", self.sub_outline),
                       ("Posición", self.sub_pos), ("Margen vertical", self.sub_margin),
                       ("Animación subtítulos", self.sub_anim), ("Color resaltado karaoke", self.highlight),
                       ("", self.part_numbers), ("", self.progress), ("Color barra", self.progress_color),
                       ("Firma (texto)", self.signature), ("Logotipo", self.logo), ("Posición firma", self.sig_pos),
                       ("Firma: inicio (s)", self.sig_start), ("Firma: duración (0 = todo)", self.sig_dur),
                       ("Animación de textos", self.text_anim), ("Archivo de fuente (drawtext)", self.font_file)):
            l4.addRow(lab, w)
        self.addTab(f4, "Textos")

    # --------------------------------------------------------------------------
    def style(self) -> SubtitleStyle:
        return SubtitleStyle(font=self.sub_font.text() or "Arial", size=self.sub_size.value(),
                             color=self.sub_color.color, outline_color=self.sub_outline_color.color,
                             outline=self.sub_outline.value(), position=self.sub_pos.currentData(),
                             margin_v=self.sub_margin.value(), animation=self.sub_anim.currentData(),
                             highlight_color=self.highlight.color)

    def set_style(self, s: SubtitleStyle) -> None:
        self.sub_font.setText(s.font)
        self.sub_size.setValue(s.size)
        self.sub_color.set(s.color)
        self.sub_outline_color.set(s.outline_color)
        self.sub_outline.setValue(s.outline)
        self.sub_pos.setCurrentIndex(max(0, self.sub_pos.findData(s.position)))
        self.sub_margin.setValue(s.margin_v)
        self.sub_anim.setCurrentIndex(max(0, self.sub_anim.findData(s.animation)))
        self.highlight.set(s.highlight_color)

    def sfx_list(self) -> list[SfxCue]:
        out = []
        for part in self.sfx.text().split(";"):
            bits = [b.strip() for b in part.split("|")]
            if len(bits) >= 2 and bits[0]:
                try:
                    out.append(SfxCue(bits[0], float(bits[1]), float(bits[2]) if len(bits) > 2 else 1.0))
                except ValueError:
                    pass
        return out

    def options(self) -> RenderOptions:
        w, h = FORMAT_TEMPLATES[self.template.currentText()]
        return RenderOptions(
            width=w, height=h, fps=self.fps.value(), crf=self.crf.value(), preset=self.preset.currentText(),
            framing=self.framing.currentData(), manual_x=self.manual_x.value(), manual_y=self.manual_y.value(),
            blur_strength=self.blur.value(), letterbox_color=self.lb_color.color,
            letterbox_image=self.lb_image.value(), frame_png=self.frame_png.value(),
            kenburns=self.kenburns.isChecked(), kb_start_zoom=self.kb_start.value(), kb_end_zoom=self.kb_end.value(),
            kb_duration=self.kb_dur.value(), brightness=self.brightness.value(), contrast=self.contrast.value(),
            saturation=self.saturation.value(), temperature=self.temperature.value(),
            stabilize=self.stabilize.isChecked(), fade_in=self.fade_in.value(), fade_out=self.fade_out.value(),
            freeze_last=self.freeze.value(), loudnorm=self.loudnorm.isChecked(), loudness_target=self.lufs.value(),
            music=self.music.value(), music_volume=self.music_vol.value(), ducking=self.ducking.isChecked(),
            sfx=self.sfx_list(), voice=self.voice.value(), voice_mode=self.voice_mode.currentData(),
            voice_offset=self.voice_offset.value(), subtitle_style=self.style(), font_file=self.font_file.value(),
            progress_color=self.progress_color.color, signature_text=self.signature.text().strip() or None,
            signature_logo=self.logo.value(), signature_position=self.sig_pos.currentText(),
            signature_start=self.sig_start.value(), signature_duration=self.sig_dur.value(),
            text_animation=self.text_anim.currentData(),
        )

    def set_options(self, o: RenderOptions) -> None:
        for name, (w, h) in FORMAT_TEMPLATES.items():
            if (w, h) == (o.width, o.height):
                self.template.setCurrentText(name)
        self.framing.setCurrentIndex(max(0, self.framing.findData(o.framing)))
        self.manual_x.setValue(o.manual_x)
        self.manual_y.setValue(o.manual_y)
        self.blur.setValue(o.blur_strength)
        self.lb_color.set(o.letterbox_color if o.letterbox_color.startswith("#") else "#000000")
        self.lb_image.set(o.letterbox_image)
        self.frame_png.set(o.frame_png)
        self.kenburns.setChecked(o.kenburns)
        self.kb_start.setValue(o.kb_start_zoom)
        self.kb_end.setValue(o.kb_end_zoom)
        self.kb_dur.setValue(o.kb_duration)
        self.fps.setValue(o.fps)
        self.crf.setValue(o.crf)
        self.preset.setCurrentText(o.preset)
        self.brightness.setValue(o.brightness)
        self.contrast.setValue(o.contrast)
        self.saturation.setValue(o.saturation)
        self.temperature.setValue(o.temperature)
        self.stabilize.setChecked(o.stabilize)
        self.fade_in.setValue(o.fade_in)
        self.fade_out.setValue(o.fade_out)
        self.freeze.setValue(o.freeze_last)
        self.loudnorm.setChecked(o.loudnorm)
        self.lufs.setValue(o.loudness_target)
        self.music.set(o.music)
        self.music_vol.setValue(o.music_volume)
        self.ducking.setChecked(o.ducking)
        self.voice.set(o.voice)
        self.voice_mode.setCurrentIndex(max(0, self.voice_mode.findData(o.voice_mode)))
        self.voice_offset.setValue(o.voice_offset)
        self.sfx.setText("; ".join(f"{s.path}|{s.time}|{s.volume}" for s in o.sfx))
        self.set_style(o.subtitle_style)
        self.font_file.set(o.font_file)
        self.progress_color.set(o.progress_color)
        self.signature.setText(o.signature_text or "")
        self.logo.set(o.signature_logo)
        self.sig_pos.setCurrentText(o.signature_position)
        self.sig_start.setValue(o.signature_start)
        self.sig_dur.setValue(o.signature_duration)
        self.text_anim.setCurrentIndex(max(0, self.text_anim.findData(o.text_animation)))

    def for_segment(self, base: RenderOptions, index: int, total: int, seg_start: float, seg_end: float,
                    story_duration: float, label: str | None) -> RenderOptions:
        """Opciones concretas de un segmento: numeración de partes y barra de progreso."""
        o = replace(base)
        if self.part_numbers.isChecked() and total > 1:
            o.part_label = label or f"Parte {index + 1}/{total}"
        if self.progress.isChecked() and story_duration > 0:
            o.progress = (seg_start / story_duration, seg_end / story_duration)
        return o
