"""Servicio de procesamiento: renderiza segmentos con caché, respaldo de hardware y subtítulos."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .config import sub_dir
from .core.models import RenderOptions, Segment
from .ffmpeg import runner
from .ffmpeg.commands import build_preview_frame_command, build_render_command, choose_encoder
from .framing.facetrack import build_face_track
from .framing.filters import vidstab_detect_command
from .library.files import (RenderCache, cache_key, ensure_space, estimate_output_size, make_temp_dir,
                            quick_fingerprint)
from .persistence.db import Database
from .subtitles import srt

log = logging.getLogger(__name__)


@dataclass
class RenderJob:
    source: str
    segment: Segment
    options: RenderOptions
    output: str
    subtitles: str | None = None  # SRT/VTT del vídeo completo (tiempos de origen)
    word_timings: str | None = None  # CSV palabra,inicio,fin para karaoke


@dataclass
class RenderResult:
    job: RenderJob
    output: str
    cached: bool
    seconds: float
    encoder: str


class Processor:
    def __init__(self, db: Database, tools: runner.Tools):
        self.db = db
        self.tools = tools
        self.cache = RenderCache(db)
        self._encoders: set[str] | None = None
        self._filters: set[str] | None = None

    @property
    def encoders(self) -> set[str]:
        if self._encoders is None:
            self._encoders = runner.available_encoders(self.tools)
        return self._encoders

    def resolve_encoder(self, opts: RenderOptions) -> RenderOptions:
        pref = self.db.get_setting("hwaccel", "auto")
        if opts.encoder != "libx264":
            return opts
        return replace(opts, encoder=choose_encoder(pref, self.encoders))

    # --- subtítulos ---------------------------------------------------------
    def prepare_subtitles(self, job: RenderJob, tmp: Path) -> str | None:
        style = job.options.subtitle_style
        if job.word_timings:
            words = srt.load_word_timings(job.word_timings)
            cues = srt.group_words(words, max_chars=style.max_chars)
            karaoke = True
        elif job.subtitles:
            cues = srt.load(job.subtitles)
            karaoke = False
        else:
            return None
        sliced = srt.slice_for_segment(cues, job.segment.kept_intervals(), job.segment.speed or 1.0)
        if not sliced:
            return None
        ass = tmp / "subs.ass"
        ass.write_text(srt.to_ass(sliced, style, job.options.width, job.options.height, karaoke), encoding="utf-8")
        return str(ass)

    # --- render -------------------------------------------------------------
    def render(self, job: RenderJob, progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None) -> RenderResult:
        t0 = time.time()
        info = runner.media_info(self.tools, job.source)
        opts = self.resolve_encoder(job.options)
        key = None
        if self.db.get_setting("cache_enabled", True):
            extra = "|".join(str(Path(p).stat().st_mtime) if p and Path(p).is_file() else ""
                             for p in (job.subtitles, job.word_timings))
            key = cache_key(quick_fingerprint(job.source), job.segment.to_dict(), opts.to_dict(), extra)
            hit = self.cache.get(key)
            if hit:
                if Path(hit).resolve() != Path(job.output).resolve():
                    import shutil
                    Path(job.output).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(hit, job.output)
                log.info("Caché: %s sin cambios, no se recodifica", Path(job.output).name)
                return RenderResult(job, job.output, True, time.time() - t0, opts.encoder)

        out_dir = Path(job.output).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        ensure_space(out_dir, estimate_output_size(job.segment.output_duration() + opts.freeze_last))
        tmp = make_temp_dir(sub_dir("tmp"))
        job_id = self.db.start_job("render", Path(job.output).name)
        try:
            if opts.framing == "face":
                track = build_face_track(job.source, job.segment, opts.width, opts.height, tmp / "face.cmd", cancel)
                opts = replace(opts, face_track_file=str(track))
            if opts.stabilize and not opts.stabilize_file:
                trf = tmp / "transforms.trf"
                try:
                    runner.run(vidstab_detect_command(self.tools.ffmpeg, job.source, job.segment.start,
                                                      job.segment.duration, str(trf)), cancel=cancel)
                    opts = replace(opts, stabilize_file=str(trf))
                except runner.FFmpegError:
                    log.warning("vidstab no disponible; se usa deshake")
            ass = self.prepare_subtitles(job, tmp)
            plan = build_render_command(self.tools.ffmpeg, job.source, job.segment, opts, job.output,
                                        has_audio=info.has_audio, subtitles_ass=ass)
            try:
                runner.run(plan.cmd, plan.duration, progress, cancel)
            except runner.FFmpegError:
                if opts.encoder == "libx264":
                    raise
                log.warning("Fallo con %s, reintentando en CPU (libx264)", opts.encoder)
                opts = replace(opts, encoder="libx264")
                plan = build_render_command(self.tools.ffmpeg, job.source, job.segment, opts, job.output,
                                            has_audio=info.has_audio, subtitles_ass=ass)
                runner.run(plan.cmd, plan.duration, progress, cancel)
            if key:
                self.cache.put(key, job.output)
            secs = time.time() - t0
            self.db.finish_job(job_id, True, secs)
            return RenderResult(job, job.output, False, secs, opts.encoder)
        except Exception as exc:
            self.db.finish_job(job_id, False, time.time() - t0, str(exc))
            raise
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def render_many(self, jobs: list[RenderJob], threads: int = 2,
                    progress: Callable[[int, float], None] | None = None,
                    cancel: threading.Event | None = None) -> list[RenderResult | Exception]:
        """Renderiza en paralelo. progress(índice, fracción)."""
        results: list[RenderResult | Exception] = [None] * len(jobs)  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
            futs = {
                pool.submit(self.render, j, (lambda f, i=i: progress(i, f)) if progress else None, cancel): i
                for i, j in enumerate(jobs)
            }
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.error("Error renderizando %s: %s", jobs[i].output, exc)
                    results[i] = exc
        return results

    def preview_frame(self, job: RenderJob, at: float, output_png: str) -> str:
        info = runner.media_info(self.tools, job.source)
        tmp = make_temp_dir(sub_dir("tmp"))
        try:
            at = min(max(at, job.segment.start), job.segment.end - 0.01)
            ass = self.prepare_subtitles(replace(job, segment=Segment(at, min(job.segment.end, at + 0.5))), tmp)
            opts = replace(job.options, framing="crop" if job.options.framing == "face" else job.options.framing,
                           stabilize=False)
            cmd = build_preview_frame_command(self.tools.ffmpeg, job.source, job.segment, opts, at, output_png,
                                              has_audio=info.has_audio, subtitles_ass=ass)
            runner.run_capture(cmd, timeout=120)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        return output_png
