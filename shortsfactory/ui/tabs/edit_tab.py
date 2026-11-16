"""Pestaña Edición: línea de tiempo, segmentos, opciones de render, previsualización y exportación."""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QMessageBox, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ... import pipeline
from ...config import sub_dir
from ...core.models import RenderOptions, Segment
from ...core.timeutil import format_timestamp, parse_timestamp, step_frames
from ...ffmpeg import runner
from ...ingest import analysis, cutting
from ...processing import RenderJob
from ...review.checks import run_checks
from ..options_panel import OptionsPanel
from ..subtitle_editor import SubtitleEditor
from ..timeline import Timeline

log = logging.getLogger(__name__)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAS_MM = True
except ImportError:  # pragma: no cover
    HAS_MM = False


class PreviewLabel(QLabel):
    """Previsualización del fotograma; arrastrar ajusta el encuadre manual."""
    dragged = Signal(float, float)

    def __init__(self):
        super().__init__("Previsualización del fotograma")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(200, 300)
        self.setStyleSheet("border:1px solid #555;")
        self._start = None

    def mousePressEvent(self, e):  # noqa: N802
        self._start = e.position()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._start is None:
            return
        dx = (self._start.x() - e.position().x()) / max(1, self.width())
        dy = (self._start.y() - e.position().y()) / max(1, self.height())
        self._start = None
        self.dragged.emit(dx, dy)


SEG_COLS = ["Inicio", "Fin", "Duración", "Salida", "Etiqueta", "Velocidad", "Volumen", "Puntuación"]


class EditTab(QWidget):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        self.src: str | None = None
        self.info: runner.MediaInfo | None = None
        self.segments: list[Segment] = []
        self.silences: list[tuple[float, float]] | None = None
        self._loading = False

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QComboBox()
        self.source.setMinimumWidth(400)
        self.source.activated.connect(lambda _: self.load(self.source.currentData()))
        btn_open = QPushButton("Abrir vídeo…")
        btn_open.clicked.connect(self.pick_source)
        top.addWidget(QLabel("Vídeo:"))
        top.addWidget(self.source, 1)
        top.addWidget(btn_open)
        root.addLayout(top)

        self.inner = QTabWidget()
        root.addWidget(self.inner, 1)
        editor = QWidget()
        self.inner.addTab(editor, "Cortes y render")
        self.subs_editor = SubtitleEditor(ctx)
        self.inner.addTab(self.subs_editor, "Editor de subtítulos")

        lay = QVBoxLayout(editor)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)

        # --- izquierda: reproductor + timeline
        left = QWidget()
        ll = QVBoxLayout(left)
        players = QHBoxLayout()
        if HAS_MM:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.video = QVideoWidget()
            self.video.setMinimumSize(320, 180)
            self.player.setVideoOutput(self.video)
            self.player.positionChanged.connect(lambda ms: self.timeline.set_playhead(ms / 1000))
            players.addWidget(self.video, 3)
        else:
            self.player = None
            players.addWidget(QLabel("QtMultimedia no disponible"), 3)
        self.preview = PreviewLabel()
        self.preview.dragged.connect(self.on_preview_drag)
        players.addWidget(self.preview, 2)
        ll.addLayout(players, 3)
        ctrl = QHBoxLayout()
        for text, fn in (("⏯", self.toggle_play), ("◀ fot.", lambda: self.step(-1)), ("fot. ▶", lambda: self.step(1)),
                         ("Marcar IN", self.mark_in), ("Marcar OUT", self.mark_out),
                         ("Añadir segmento IN–OUT", self.add_from_marks), ("Previsualizar fotograma", self.preview_frame)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            ctrl.addWidget(b)
        self.time_label = QLabel("00:00:00.000")
        ctrl.addWidget(self.time_label)
        ll.addLayout(ctrl)
        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek)
        self.timeline.segment_selected.connect(lambda i: self.table.selectRow(i))
        ll.addWidget(self.timeline, 1)
        ll.addWidget(QLabel("Clic: mover cabezal · Ctrl+clic: seleccionar segmento · Clic derecho: IN/OUT · "
                            "Rueda: zoom · Atajos configurables en Ajustes"))
        split.addWidget(left)

        # --- derecha: segmentos + opciones
        right = QWidget()
        rl = QVBoxLayout(right)
        cut_box = QGroupBox("Cortes")
        g = QGridLayout(cut_box)
        actions = [
            ("Trocear en partes", self.split_parts), ("Cortes para Shorts", self.split_shorts),
            ("Sugerir cortes (planos)", self.suggest), ("Importar capítulos TXT", self.import_chapters),
            ("Importar marcas CSV", self.import_marks), ("Ajustar a duración…", self.fit_duration),
            ("Duplicar con otra duración…", self.duplicate), ("Quitar pausas largas", self.remove_pauses),
            ("Quitar silencio inicio/fin", self.trim_edges), ("Eliminar segmento", self.delete_segment),
        ]
        for i, (text, fn) in enumerate(actions):
            b = QPushButton(text)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        rl.addWidget(cut_box)
        self.table = QTableWidget(0, len(SEG_COLS))
        self.table.setHorizontalHeaderLabels(SEG_COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemChanged.connect(self.on_table_edit)
        self.table.itemSelectionChanged.connect(self.on_select)
        rl.addWidget(self.table, 3)
        self.options = OptionsPanel()
        rl.addWidget(self.options, 2)

        exp = QGroupBox("Exportar")
        el = QGridLayout(exp)
        self.channel = QComboBox()
        self.out_dir = QLabel()
        b_out = QPushButton("Carpeta de salida…")
        b_out.clicked.connect(self.pick_out_dir)
        b_sel = QPushButton("Exportar seleccionados")
        b_sel.clicked.connect(lambda: self.export(selected_only=True))
        b_all = QPushButton("Exportar todo")
        b_all.clicked.connect(lambda: self.export(selected_only=False))
        b_q = QPushButton("Exportar todo y encolar")
        b_q.clicked.connect(lambda: self.export(selected_only=False, enqueue=True))
        b_save = QPushButton("Guardar opciones como predeterminadas")
        b_save.clicked.connect(self.save_defaults)
        el.addWidget(QLabel("Encolar en canal:"), 0, 0)
        el.addWidget(self.channel, 0, 1)
        el.addWidget(b_out, 1, 0)
        el.addWidget(self.out_dir, 1, 1)
        el.addWidget(b_sel, 2, 0)
        el.addWidget(b_all, 2, 1)
        el.addWidget(b_q, 3, 0)
        el.addWidget(b_save, 3, 1)
        rl.addWidget(exp)
        split.addWidget(right)
        split.setSizes([700, 600])

        self.options.set_options(RenderOptions.from_dict(ctx.db.get_setting("render_defaults", {}) or {}))
        self.setup_shortcuts()
        self.refresh_sources()

    # --- configuración --------------------------------------------------------
    def setup_shortcuts(self):
        sc = self.ctx.db.get_setting("shortcuts", {}) or {}
        mapping = {
            "mark_in": self.mark_in, "mark_out": self.mark_out, "frame_next": lambda: self.step(1),
            "frame_prev": lambda: self.step(-1), "second_next": lambda: self.step_seconds(1),
            "second_prev": lambda: self.step_seconds(-1), "play_pause": self.toggle_play,
            "add_segment": self.add_from_marks,
        }
        self._shortcuts = []
        for key, fn in mapping.items():
            if sc.get(key):
                s = QShortcut(QKeySequence(sc[key]), self)
                s.setContext(Qt.WidgetWithChildrenShortcut)
                s.activated.connect(fn)
                self._shortcuts.append(s)

    def refresh_sources(self):
        cur = self.source.currentData()
        self.source.clear()
        for p in self.ctx.db.get_setting("sources", []) or []:
            self.source.addItem(Path(p).name, p)
        if cur:
            self.source.setCurrentIndex(max(0, self.source.findData(cur)))
        self.channel.clear()
        self.channel.addItem("(no encolar)", None)
        for c in self.ctx.channels.list():
            self.channel.addItem(f"{c.name} ({c.language})", c.id)
        self.out_dir.setText(self.ctx.db.get_setting("output_dir", "") or str(sub_dir("exports")))

    def pick_source(self):
        f, _ = QFileDialog.getOpenFileName(self, "Abrir vídeo", "", "Vídeos (*.mp4 *.mov *.mkv *.webm *.avi)")
        if f:
            srcs = self.ctx.db.get_setting("sources", []) or []
            if f not in srcs:
                srcs.append(f)
                self.ctx.db.set_setting("sources", srcs)
            self.refresh_sources()
            self.source.setCurrentIndex(self.source.findData(f))
            self.load(f)

    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de salida", self.out_dir.text())
        if d:
            self.ctx.db.set_setting("output_dir", d)
            self.out_dir.setText(d)

    def save_defaults(self):
        self.ctx.db.set_setting("render_defaults", self.options.options().to_dict())
        QMessageBox.information(self, "Guardado", "Opciones guardadas como predeterminadas.")

    # --- carga ----------------------------------------------------------------
    def load(self, path: str | None):
        if not path:
            return
        self.save_project()
        self.src = path
        self.silences = None
        proj = self.ctx.db.get_setting(f"project:{path}", {}) or {}
        self.segments = [Segment.from_dict(d) for d in proj.get("segments", [])]
        if proj.get("options"):
            self.options.set_options(RenderOptions.from_dict(proj["options"]))
        self.refresh_table()
        if self.player:
            self.player.setSource(QUrl.fromLocalFile(path))
        srt = Path(path).with_suffix(".srt")
        if srt.is_file() and not self.options.subs.value():
            self.options.subs.set(str(srt))
            self.subs_editor.open(str(srt))

        def work(progress, cancel):
            info = runner.media_info(self.ctx.tools, path)
            progress(0.3)
            samples = analysis.pcm_samples(self.ctx.tools, path)
            progress(0.9)
            return info, analysis.waveform_peaks(samples, 6000)

        def done(res):
            self.info, peaks = res
            self.timeline.set_media(self.info.duration, peaks)
            self.timeline.set_segments(self.segments)

        self.tasks.start(f"Analizando {Path(path).name}", work, on_done=done, on_error=self.error)

    def save_project(self):
        if self.src:
            self.ctx.db.set_setting(f"project:{self.src}", {
                "segments": [s.to_dict() for s in self.segments], "options": self.options.options().to_dict()})

    def project_data(self) -> dict:
        self.save_project()
        return {"source": self.src, **(self.ctx.db.get_setting(f"project:{self.src}", {}) if self.src else {})}

    def error(self, msg: str):
        log.error(msg)
        QMessageBox.critical(self, "Error", msg[:2000])

    # --- reproductor ----------------------------------------------------------
    def current_time(self) -> float:
        return self.player.position() / 1000 if self.player else self.timeline.playhead

    def seek(self, t: float):
        if self.player:
            self.player.setPosition(int(t * 1000))
        self.timeline.set_playhead(t)
        self.time_label.setText(format_timestamp(t))

    def toggle_play(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def fps(self) -> float:
        return self.info.fps if self.info and self.info.fps else 30.0

    def step(self, frames: int):
        if self.player:
            self.player.pause()
        self.seek(step_frames(self.current_time(), frames, self.fps()))

    def step_seconds(self, s: float):
        self.seek(max(0.0, self.current_time() + s))

    def mark_in(self):
        self.timeline.mark_in = self.current_time()
        self.timeline.update()

    def mark_out(self):
        self.timeline.mark_out = self.current_time()
        self.timeline.update()

    def add_from_marks(self):
        a, b = self.timeline.mark_in, self.timeline.mark_out
        if a is None or b is None or b <= a:
            QMessageBox.information(self, "Marcas", "Marca IN y OUT (OUT posterior a IN).")
            return
        self.segments.append(Segment(a, b, label=f"Manual {len(self.segments) + 1}"))
        self.segments.sort(key=lambda s: s.start)
        self.timeline.mark_in = self.timeline.mark_out = None
        self.refresh_table()

    # --- tabla de segmentos -----------------------------------------------------
    def refresh_table(self):
        self._loading = True
        self.table.setRowCount(len(self.segments))
        for i, s in enumerate(self.segments):
            vals = [format_timestamp(s.start), format_timestamp(s.end), f"{s.duration:.2f}",
                    f"{s.output_duration():.2f}", s.label, f"{s.speed:.2f}", f"{s.volume:.2f}",
                    "" if s.score is None else f"{s.score:.3f}"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (2, 3, 7):
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, c, it)
        self._loading = False
        self.timeline.set_segments(self.segments)
        self.save_project()

    def on_table_edit(self, item):
        if self._loading:
            return
        i, c = item.row(), item.column()
        s = self.segments[i]
        try:
            if c == 0:
                s.start = parse_timestamp(item.text())
            elif c == 1:
                s.end = parse_timestamp(item.text())
            elif c == 4:
                s.label = item.text()
            elif c == 5:
                v = float(item.text().replace(",", "."))
                if not 0.5 <= v <= 2.0:
                    raise ValueError("la velocidad debe estar entre 0,5 y 2")
                s.speed = v
            elif c == 6:
                s.volume = max(0.0, float(item.text().replace(",", ".")))
        except ValueError as exc:
            QMessageBox.warning(self, "Valor no válido", str(exc))
        self.refresh_table()

    def selected_indices(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def on_select(self):
        idx = self.selected_indices()
        if idx:
            self.timeline.selected = idx[0]
            self.seek(self.segments[idx[0]].start)

    def delete_segment(self):
        for i in reversed(self.selected_indices()):
            del self.segments[i]
        self.refresh_table()

    # --- operaciones de corte ---------------------------------------------------
    def need_source(self) -> bool:
        if not self.src or not self.info:
            QMessageBox.information(self, "Vídeo", "Carga un vídeo primero (y espera al análisis).")
            return False
        return True

    def with_silences(self, then):
        if self.silences is not None:
            then(self.silences)
            return
        db = self.ctx.db

        def work(p, c):
            return analysis.detect_silences(self.ctx.tools, self.src, db.get_setting("silence_db", -35),
                                            db.get_setting("silence_min", 0.4), self.info.duration, c)

        def done(sil):
            self.silences = sil
            then(sil)

        self.tasks.start("Detectando silencios", work, on_done=done, on_error=self.error)

    def split_parts(self):
        if not self.need_source():
            return
        size, ok = QInputDialog.getDouble(self, "Partes", "Duración de cada parte (s):",
                                          float(self.ctx.db.get_setting("part_seconds", 180)), 10, 3600, 1)
        if not ok:
            return
        if self.ctx.db.get_setting("clean_cuts", True):
            tol = float(self.ctx.db.get_setting("clean_cut_tolerance", 8))
            self.with_silences(lambda sil: self._set(cutting.split_clean(self.info.duration, size, sil, tol)))
        else:
            self._set(cutting.split_fixed(self.info.duration, size))

    def split_shorts(self):
        if not self.need_source():
            return
        size, ok = QInputDialog.getDouble(self, "Shorts", "Duración objetivo (15–60 s):", 45, 15, 60, 1)
        if ok:
            self.with_silences(lambda sil: self._set(cutting.split_shorts(self.info.duration, size, sil)))

    def _set(self, segs):
        self.segments = segs
        self.refresh_table()

    def suggest(self):
        if not self.need_source():
            return
        thr = float(self.ctx.db.get_setting("scene_threshold", 0.35))
        src, tools = self.src, self.ctx.tools

        def work(progress, cancel):
            scenes = analysis.detect_scenes(tools, src, thr, cancel)
            progress(0.4)
            motion = analysis.motion_profile(tools, src)
            progress(0.7)
            loud = analysis.loudness_profile(analysis.pcm_samples(tools, src))
            cands = cutting.candidates_from_scenes(scenes, self.info.duration,
                                                   float(self.ctx.db.get_setting("short_min", 15)),
                                                   float(self.ctx.db.get_setting("short_max", 60)))
            return scenes, cutting.score_candidates(cands, loud, motion)

        def done(res):
            scenes, ranked = res
            self.timeline.scenes = scenes
            if not ranked:
                QMessageBox.information(self, "Sugerencias", "No se encontraron candidatos con la duración pedida.")
            self._set(ranked)

        self.tasks.start("Sugerir cortes (planos, volumen, movimiento)", work, on_done=done, on_error=self.error)

    def import_chapters(self):
        f, _ = QFileDialog.getOpenFileName(self, "Capítulos", "", "Texto (*.txt)")
        if f:
            self._set(cutting.parse_chapters_txt(Path(f).read_text(encoding="utf-8-sig"),
                                                 self.info.duration if self.info else None))

    def import_marks(self):
        f, _ = QFileDialog.getOpenFileName(self, "Marcas", "", "CSV (*.csv)")
        if f:
            self._set(cutting.parse_marks_csv(Path(f).read_text(encoding="utf-8-sig")))

    def fit_duration(self):
        idx = self.selected_indices() or list(range(len(self.segments)))
        target, ok = QInputDialog.getDouble(self, "Ajustar", "Duración final exacta (s):", 59, 1, 3600, 2)
        if ok:
            for i in idx:
                self.segments[i] = cutting.fit_to_duration(self.segments[i], target)
            self.refresh_table()

    def duplicate(self):
        idx = self.selected_indices()
        if not idx:
            return
        d, ok = QInputDialog.getDouble(self, "Duplicar", "Nueva duración (s):", 30, 1, 3600, 1)
        if ok:
            for i in idx:
                self.segments.append(cutting.duplicate_with_duration(self.segments[i], d,
                                                                     self.info.duration if self.info else None))
            self.segments.sort(key=lambda s: s.start)
            self.refresh_table()

    def remove_pauses(self):
        idx = self.selected_indices() or list(range(len(self.segments)))
        thr = float(self.ctx.db.get_setting("pause_threshold", 1.2))

        def apply(sil):
            for i in idx:
                self.segments[i] = cutting.remove_pauses(self.segments[i], sil, thr)
            self.refresh_table()

        if self.need_source():
            self.with_silences(apply)

    def trim_edges(self):
        idx = self.selected_indices() or list(range(len(self.segments)))

        def apply(sil):
            for i in idx:
                self.segments[i] = cutting.trim_edge_silence(self.segments[i], sil)
            self.refresh_table()

        if self.need_source():
            self.with_silences(apply)

    # --- previsualización y exportación -------------------------------------------
    def on_preview_drag(self, dx: float, dy: float):
        if self.options.framing.currentData() != "manual":
            self.options.framing.setCurrentIndex(self.options.framing.findData("manual"))
        self.options.manual_x.setValue(min(1, max(0, self.options.manual_x.value() + dx * 2)))
        self.options.manual_y.setValue(min(1, max(0, self.options.manual_y.value() + dy * 2)))
        self.preview_frame()

    def _jobs(self, indices: list[int]) -> list[RenderJob]:
        base = self.options.options()
        if self.options.autocrop.isChecked():
            crop = analysis.detect_black_bars(self.ctx.tools, self.src, self.info.duration)
            if crop and (crop[0] < self.info.width or crop[1] < self.info.height):
                base = replace(base, autocrop=crop)
        return pipeline.build_jobs(self.src, self.segments, base, self.out_dir.text(), self.info.duration,
                                   self.options.subs.value(), self.options.words.value(),
                                   self.options.part_numbers.isChecked(), self.options.progress.isChecked(), indices)

    def preview_frame(self):
        if not self.need_source() or not self.segments:
            return
        idx = (self.selected_indices() or [0])[0]
        t = self.current_time()
        seg = self.segments[idx]
        if not seg.start <= t <= seg.end:
            t = seg.start + min(1.0, seg.duration / 2)
        job = pipeline.build_jobs(self.src, self.segments, self.options.options(), self.out_dir.text(),
                                  self.info.duration, self.options.subs.value(), self.options.words.value(),
                                  self.options.part_numbers.isChecked(), self.options.progress.isChecked(), [idx])[0]
        png = str(sub_dir("tmp") / "preview.png")

        def done(path):
            pm = QPixmap(path)
            self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.tasks.start("Previsualizando fotograma", lambda p, c: self.ctx.processor.preview_frame(job, t, png),
                         on_done=done, on_error=self.error)

    def export(self, selected_only: bool, enqueue: bool = False):
        if not self.need_source() or not self.segments:
            return
        indices = self.selected_indices() if selected_only else list(range(len(self.segments)))
        if not indices:
            return
        channel_id = self.channel.currentData() if enqueue or self.channel.currentData() else None
        if enqueue and not channel_id:
            QMessageBox.information(self, "Canal", "Elige el canal en el que encolar.")
            return
        is_short = all(self.segments[i].kind == "short" for i in indices)
        threads = int(self.ctx.db.get_setting("threads", 2))
        ctx = self.ctx

        def work(progress, cancel):
            jobs = self._jobs(indices)
            fr = [0.0] * len(jobs)

            def prog(i, f):
                fr[i] = f
                progress(sum(fr) / len(fr))

            results = ctx.processor.render_many(jobs, threads, prog, cancel)
            qc = []
            for r in results:
                if not isinstance(r, Exception):
                    rep = run_checks(ctx.tools, r.output, is_short=is_short, width=r.job.options.width,
                                     height=r.job.options.height, freeze_tail=r.job.options.freeze_last)
                    for issue in rep.issues:
                        qc.append(f"{Path(r.output).name}: {issue.level}: {issue.message}")
            queued = pipeline.enqueue_results(ctx, results, channel_id) if channel_id else []
            return results, qc, queued

        def done(res):
            results, qc, queued = res
            ok = sum(1 for r in results if not isinstance(r, Exception))
            errs = [str(r) for r in results if isinstance(r, Exception)]
            msg = f"Exportados {ok}/{len(results)} vídeos."
            if queued:
                msg += f"\nEncolados {len(queued)} en el canal (pendientes de revisión)."
            if qc:
                msg += "\n\nComprobaciones técnicas:\n" + "\n".join(qc[:20])
            if errs:
                msg += "\n\nErrores:\n" + "\n".join(e[:300] for e in errs)
            QMessageBox.information(self, "Exportación", msg)
            if self.main:
                self.main.refresh_all()

        self.tasks.start(f"Exportando {len(indices)} vídeo(s)", work, on_done=done, on_error=self.error)
