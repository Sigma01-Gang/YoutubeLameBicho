"""Pruebas de integración con un vídeo de ejemplo corto generado con FFmpeg."""
import json
import socket
import subprocess

import pytest

from conftest import needs_ffmpeg
from shortsfactory.core.models import RenderOptions, Segment
from shortsfactory.ffmpeg import runner
from shortsfactory.ingest import analysis, cutting
from shortsfactory.processing import Processor, RenderJob
from shortsfactory.review.checks import run_checks

pytestmark = needs_ffmpeg


def stream_durations(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}


@pytest.fixture
def no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("conexión de red durante el procesamiento")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def test_split_render_parts_keeps_sync(db, sample_video, tmp_path, no_network):
    tools = runner.Tools()
    db.set_setting("hwaccel", "none")
    info = runner.media_info(tools, str(sample_video))
    silences = analysis.detect_silences(tools, str(sample_video), total=info.duration)
    assert any(27 < a < 29 for a, _ in silences)
    segs = cutting.split_clean(info.duration, 25, silences, tolerance=6)
    assert len(segs) == 3
    assert segs[0].end == pytest.approx(30, abs=0.3)  # cortado en el centro del silencio (28-32)

    srt_path = tmp_path / "subs.srt"
    srt_path.write_text("1\n00:00:31,000 --> 00:00:33,000\nPrimera de la parte 2\n", encoding="utf-8")
    proc = Processor(db, tools)
    opts = RenderOptions(framing="blur", preset="ultrafast", freeze_last=1.0, fade_out=0.5)
    jobs = [RenderJob(str(sample_video), s, opts, str(tmp_path / f"parte{i + 1}.mp4"), subtitles=str(srt_path))
            for i, s in enumerate(segs)]
    results = proc.render_many(jobs, threads=2)
    for job, res in zip(jobs, results):
        assert not isinstance(res, Exception), res
        i = runner.media_info(tools, res.output)
        assert (i.width, i.height) == (1080, 1920)
        expected = job.segment.output_duration() + 1.0
        assert i.duration == pytest.approx(expected, abs=0.1)
        d = stream_durations(res.output)
        assert abs(d["video"] - d["audio"]) < 0.1  # audio y vídeo sincronizados
    # la caché evita recodificar
    again = proc.render(jobs[0])
    assert again.cached


def test_short_with_pause_removal_and_qc(db, sample_video, tmp_path):
    tools = runner.Tools()
    db.set_setting("hwaccel", "none")
    silences = analysis.detect_silences(tools, str(sample_video), total=70)
    seg = cutting.remove_pauses(Segment(10, 75, kind="short"), silences, threshold=1.0)
    seg = cutting.fit_to_duration(Segment(seg.start, min(seg.end, 70), keep=seg.keep), 59)
    assert seg.output_duration() <= 59
    out = tmp_path / "short.mp4"
    Processor(db, tools).render(RenderJob(str(sample_video), seg, RenderOptions(framing="crop", preset="ultrafast"),
                                          str(out)))
    rep = run_checks(tools, str(out), is_short=True)
    assert rep.info.duration <= 60 and (rep.info.width, rep.info.height) == (1080, 1920)
    assert not any("resolución" in i.message or "supera" in i.message for i in rep.issues)


def test_face_tracking_runs_without_faces(db, sample_video, tmp_path):
    pytest.importorskip("cv2")
    out = tmp_path / "face.mp4"
    Processor(db, runner.Tools()).render(RenderJob(str(sample_video), Segment(0, 4),
                                                   RenderOptions(framing="face", preset="ultrafast"), str(out)))
    assert runner.media_info(runner.Tools(), str(out)).height == 1920


def test_preview_frame(db, sample_video, tmp_path):
    png = tmp_path / "p.png"
    Processor(db, runner.Tools()).preview_frame(
        RenderJob(str(sample_video), Segment(0, 10), RenderOptions(part_label="Parte 1/3"), "x.mp4"), 2, str(png))
    assert png.stat().st_size > 1000
