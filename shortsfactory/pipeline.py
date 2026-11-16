"""Flujo completo sin interfaz: trocear, renderizar, comprobar y encolar (también «modo de un clic»)."""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .config import sub_dir
from .core.models import RenderOptions, Segment
from .ffmpeg import runner
from .ingest import analysis, cutting
from .library.files import check_source_integrity
from .processing import RenderJob, RenderResult
from .review.checks import run_checks
from .sheets.sheets import extract_vars, render_template

log = logging.getLogger(__name__)


def output_name(stem: str, seg: Segment, index: int, total: int) -> str:
    if seg.kind == "short":
        return f"{stem}_short{index + 1}.mp4"
    return f"{stem}_parte{index + 1}_de{total}.mp4"


def plan_segments(ctx, src: str, mode: str = "parts", cancel: threading.Event | None = None) -> list[Segment]:
    """mode: 'parts' (partes de N s) o 'shorts' (cortes de 15-60 s)."""
    db, tools = ctx.db, ctx.tools
    info = runner.media_info(tools, src)
    silences: list = []
    if db.get_setting("clean_cuts", True):
        silences = analysis.detect_silences(tools, src, db.get_setting("silence_db", -35),
                                            db.get_setting("silence_min", 0.4), info.duration, cancel)
    if mode == "shorts":
        segs = cutting.split_shorts(info.duration, float(db.get_setting("short_max", 60)) - 1, silences,
                                    float(db.get_setting("short_min", 15)), float(db.get_setting("short_max", 60)))
    elif silences:
        segs = cutting.split_clean(info.duration, float(db.get_setting("part_seconds", 180)), silences,
                                   float(db.get_setting("clean_cut_tolerance", 8.0)))
    else:
        segs = cutting.split_fixed(info.duration, float(db.get_setting("part_seconds", 180)))
    if silences:
        segs = [cutting.trim_edge_silence(s, silences) for s in segs]
    return segs


def segment_options(base: RenderOptions, segs: list[Segment], index: int, story_duration: float,
                    numbering: bool = True, progress_bar: bool = True) -> RenderOptions:
    o = replace(base)
    s = segs[index]
    if numbering and len(segs) > 1 and s.kind != "short":
        o.part_label = s.label or f"Parte {index + 1}/{len(segs)}"
    if progress_bar and story_duration > 0:
        o.progress = (s.start / story_duration, s.end / story_duration)
    return o


def build_jobs(src: str, segs: list[Segment], base: RenderOptions, out_dir: str, story_duration: float,
               subtitles: str | None = None, word_timings: str | None = None, numbering: bool = True,
               progress_bar: bool = True, indices: list[int] | None = None) -> list[RenderJob]:
    stem = Path(src).stem
    jobs = []
    for i in indices if indices is not None else range(len(segs)):
        jobs.append(RenderJob(src, segs[i], segment_options(base, segs, i, story_duration, numbering, progress_bar),
                              str(Path(out_dir) / output_name(stem, segs[i], i, len(segs))),
                              subtitles, word_timings))
    return jobs


def enqueue_results(ctx, results: list[RenderResult | Exception], channel_id: int) -> list[int]:
    """Encola los vídeos renderizados con título/descripción/etiquetas de las plantillas del canal."""
    ch = ctx.channels.get(channel_id)
    blocks = ctx.db.get_setting("blocks", {}) or {}
    ids = []
    for res in results:
        if isinstance(res, Exception) or res is None:
            continue
        vars_ = extract_vars(res.output)
        vars_["canal"] = ch.name if ch else ""
        t = ch.templates if ch else {}
        is_short = res.job.segment.output_duration() + res.job.options.freeze_last <= 60
        ids.append(ctx.queue.add(
            channel_id, res.output,
            render_template(t.get("titulo", "{titulo}"), vars_, blocks)[:100],
            render_template(t.get("descripcion", ""), vars_, blocks)[:5000],
            render_template(t.get("etiquetas", ""), vars_, blocks), is_short=is_short,
        ))
    return ids


def one_click(ctx, src: str, channel_id: int | None, mode: str = "parts",
              progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None) -> dict:
    """Procesa y encola con los ajustes por defecto."""
    ok, why = check_source_integrity(ctx.tools, src)
    if not ok:
        raise RuntimeError(f"Archivo de origen no válido: {why}")
    info = runner.media_info(ctx.tools, src)
    segs = plan_segments(ctx, src, mode, cancel)
    base = RenderOptions.from_dict(ctx.db.get_setting("render_defaults", {}) or {})
    out_dir = ctx.db.get_setting("output_dir", "") or str(sub_dir("exports"))
    out_dir = str(Path(out_dir) / Path(src).stem)
    subs = str(Path(src).with_suffix(".srt")) if Path(src).with_suffix(".srt").is_file() else None
    jobs = build_jobs(src, segs, base, out_dir, info.duration, subs)
    done = [0.0] * len(jobs)

    def prog(i, f):
        done[i] = f
        if progress:
            progress(sum(done) / len(done))

    results = ctx.processor.render_many(jobs, int(ctx.db.get_setting("threads", 2)), prog, cancel)
    qc = {}
    for r in results:
        if not isinstance(r, Exception):
            rep = run_checks(ctx.tools, r.output, is_short=(mode == "shorts"), width=base.width, height=base.height,
                             freeze_tail=base.freeze_last)
            qc[r.output] = [f"{i.level}: {i.message}" for i in rep.issues]
    queued = enqueue_results(ctx, results, channel_id) if channel_id else []
    return {"segments": len(segs), "rendered": sum(1 for r in results if not isinstance(r, Exception)),
            "errors": [str(r) for r in results if isinstance(r, Exception)], "queued": queued, "qc": qc}
