import pytest
from shortsfactory.core.models import RenderOptions, Segment, SfxCue
from shortsfactory.ffmpeg.commands import build_render_command, choose_encoder, select_expression
from shortsfactory.ffmpeg.graph import escape_path, escape_text, ff_color
from shortsfactory.ffmpeg.runner import parse_encoders, parse_probe


def fg(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_basic_command_shape():
    plan = build_render_command("ffmpeg", "in.mp4", Segment(10, 70), RenderOptions(framing="crop"), "out.mp4")
    cmd = plan.cmd
    assert cmd[cmd.index("-ss") + 1] == "10" and cmd[cmd.index("-t") + 1] == "60"
    assert "-map" in cmd and "[vout]" in cmd and "[aout]" in cmd
    assert "libx264" in cmd and cmd[-1] == "out.mp4"
    g = fg(cmd)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in g
    assert "crop=1080:1920" in g and "loudnorm=I=-14" in g
    assert plan.duration == pytest.approx(60)


def test_blur_letterbox_face_modes():
    g = fg(build_render_command("ffmpeg", "a.mp4", Segment(0, 5), RenderOptions(framing="blur"), "o.mp4").cmd)
    assert "split=2" in g and "boxblur" in g and "overlay=(W-w)/2:(H-h)/2" in g
    g = fg(build_render_command("ffmpeg", "a.mp4", Segment(0, 5),
                                RenderOptions(framing="letterbox", letterbox_color="#112233"), "o.mp4").cmd)
    assert "color=c=0x112233:s=1080x1920" in g
    g = fg(build_render_command("ffmpeg", "a.mp4", Segment(0, 5),
                                RenderOptions(framing="face", face_track_file="/tmp/t.cmd"), "o.mp4").cmd)
    assert "sendcmd=f='/tmp/t.cmd'" in g and "crop@face=1080:1920" in g


def test_format_templates_and_quality():
    o = RenderOptions(width=1080, height=1080, crf=18, fps=30)
    cmd = build_render_command("ffmpeg", "a.mp4", Segment(0, 5), o, "o.mp4").cmd
    assert "crop=1080:1080" in fg(cmd)
    assert cmd[cmd.index("-crf") + 1] == "18" and cmd[cmd.index("-r") + 1] == "30"


def test_effects_freeze_fade_speed_pauses():
    seg = Segment(0, 30, speed=1.5, keep=[(0, 10), (12, 30)])
    o = RenderOptions(fade_in=0.5, fade_out=1.0, freeze_last=2.0, kenburns=True, brightness=0.1, temperature=5000,
                      stabilize=True, stabilize_file="t.trf", autocrop=(1920, 800, 0, 140))
    plan = build_render_command("ffmpeg", "a.mp4", seg, o, "o.mp4")
    g = plan.filtergraph
    assert "select='between(t\\,0\\,10)+between(t\\,12\\,30)'" in g and "aselect=" in g
    assert "setpts=PTS/1.5" in g and "atempo=1.5" in g
    assert "tpad=stop_mode=clone:stop_duration=2" in g and "apad=pad_dur=2" in g
    assert plan.duration == pytest.approx(28 / 1.5 + 2)
    assert "fade=t=in:st=0:d=0.5" in g and "afade=t=out" in g
    assert "zoompan=" in g and "colortemperature=temperature=5000" in g and "eq=brightness=0.1" in g
    assert "vidstabtransform=input='t.trf'" in g and "crop=1920:800:0:140" in g


def test_audio_music_ducking_sfx_voice():
    o = RenderOptions(music="m.mp3", ducking=True, sfx=[SfxCue("hit.wav", 2.5, 0.8)], voice="v.wav", voice_mode="mix")
    cmd = build_render_command("ffmpeg", "a.mp4", Segment(0, 20), o, "o.mp4").cmd
    g = fg(cmd)
    assert "-stream_loop" in cmd and "m.mp3" in cmd and "hit.wav" in cmd and "v.wav" in cmd
    assert "sidechaincompress" in g and "adelay=2500|2500" in g and "amix=inputs=2" in g
    o2 = RenderOptions(voice="v.wav", voice_mode="replace", loudnorm=False)
    g2 = fg(build_render_command("ffmpeg", "a.mp4", Segment(0, 20), o2, "o.mp4").cmd)
    assert "apad=whole_dur=20" in g2 and "loudnorm" not in g2


def test_overlays_text_frame_logo_subs():
    o = RenderOptions(part_label="Parte 1/5", progress=(0.0, 0.2), signature_text="@mi:canal",
                      frame_png="marco.png", signature_logo="logo.png", font_file="C:\\Fonts\\a.ttf")
    plan = build_render_command("ffmpeg", "a.mp4", Segment(0, 20), o, "o.mp4", subtitles_ass="C:\\x\\s.ass")
    g = plan.filtergraph
    assert "text='Parte 1/5'" in g and "fontfile='C\\:/Fonts/a.ttf'" in g
    assert "@mi\\:canal" in g and "ass=filename='C\\:/x/s.ass'" in g
    assert "overlay=x='-w+w*(0+(0.2)*min(t/20\\,1))'" in g
    assert "marco.png" in plan.cmd and "logo.png" in plan.cmd


def test_no_audio_source_uses_silence():
    g = fg(build_render_command("ffmpeg", "a.mp4", Segment(0, 5), RenderOptions(), "o.mp4", has_audio=False).cmd)
    assert "anullsrc" in g and "[0:a]" not in g


def test_invalid_segments():
    with pytest.raises(ValueError):
        build_render_command("ffmpeg", "a.mp4", Segment(5, 5), RenderOptions(), "o.mp4")
    with pytest.raises(ValueError):
        build_render_command("ffmpeg", "a.mp4", Segment(0, 5, speed=3), RenderOptions(), "o.mp4")


def test_encoder_choice_and_args():
    assert choose_encoder("auto", {"libx264", "h264_qsv"}) == "h264_qsv"
    assert choose_encoder("nvenc", {"libx264"}) == "libx264"
    assert choose_encoder("none", {"h264_nvenc"}) == "libx264"
    cmd = build_render_command("ffmpeg", "a", Segment(0, 1), RenderOptions(encoder="h264_nvenc", crf=23), "o").cmd
    assert cmd[cmd.index("-cq") + 1] == "23"
    assert parse_encoders(" V....D libx264  H.264\n V..... h264_qsv  QSV\n ------\n") == {"libx264", "h264_qsv"}


def test_escaping_helpers():
    assert escape_path("C:\\a b\\it's.srt") == "'C\\:/a b/it\\'s.srt'"
    assert escape_text("50% a:b") == "'50\\% a\\:b'"
    assert ff_color("#ff0000") == "0xff0000" and ff_color("black") == "black"
    assert select_expression(Segment(5, 10)) is None


def test_inputs_are_local_only():
    """Criterio: el procesamiento no hace conexiones de red (todas las entradas son archivos locales)."""
    o = RenderOptions(music="m.mp3", frame_png="f.png")
    plan = build_render_command("ffmpeg", "a.mp4", Segment(0, 3), o, "o.mp4")
    assert all("://" not in i for i in plan.inputs)


def test_parse_probe():
    info = parse_probe({"streams": [{"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1",
                                     "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac"}],
                        "format": {"duration": "59.5", "bit_rate": "8000000"}})
    assert (info.width, info.height, info.fps, info.duration, info.has_audio) == (1080, 1920, 30.0, 59.5, True)
