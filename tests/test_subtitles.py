import pytest
from shortsfactory.core.models import SubtitleStyle
from shortsfactory.subtitles import srt

SRT = """1
00:00:01,000 --> 00:00:03,500
Hola <i>mundo</i>

2
00:00:04,000 --> 00:00:06,000
Segunda línea
con dos renglones
"""


def test_parse_and_roundtrip_srt_vtt(tmp_path):
    cues = srt.parse_srt(SRT)
    assert len(cues) == 2 and cues[0].text == "Hola mundo" and cues[1].end == 6.0
    vtt = srt.to_vtt(cues)
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:03.500" in vtt
    back = srt.parse_vtt(vtt)
    assert [(c.start, c.end, c.text) for c in back] == [(c.start, c.end, c.text) for c in cues]
    p = tmp_path / "a.srt"
    p.write_text(SRT, encoding="utf-8")
    out = srt.convert(p, tmp_path / "a.vtt")
    assert srt.load(out)[1].text == "Segunda línea\ncon dos renglones"


def test_shift_split_merge():
    cues = srt.parse_srt(SRT)
    shifted = srt.shift(cues, -1.5)
    assert shifted[0].start == 0 and shifted[0].end == 2.0
    split = srt.split_cue(cues, 1)
    assert len(split) == 3 and split[1].end == split[2].start
    merged = srt.merge_cues(split, 1)
    assert len(merged) == 2 and merged[1].start == 4.0 and merged[1].end == 6.0


def test_validation_warnings():
    cues = srt.parse_srt(SRT) + [srt.Cue(5.5, 7, "x" * 60)]
    warns = srt.validate(cues, max_chars=42, max_lines=1)
    msgs = " | ".join(w.message for w in warns)
    assert "2 líneas" in msgs and "60 caracteres" in msgs and "solapa" in msgs


def test_glossary():
    cues = [srt.Cue(0, 1, "Llegó mariana con Grehory")]
    warns = srt.check_glossary(cues, ["Mariana", "Gregory"])
    msgs = " ".join(w.message for w in warns)
    assert "Mariana" in msgs and "Gregory" in msgs


def test_slice_keeps_sync_with_pauses_and_speed():
    cues = [srt.Cue(100, 102, "A"), srt.Cue(110, 112, "B"), srt.Cue(150, 160, "fuera")]
    kept = [(100, 105), (108, 120)]  # se eliminan 105–108
    out = srt.slice_for_segment(cues, kept, speed=1.0)
    assert [(c.start, c.end) for c in out] == [(0, 2), (7, 9)]
    out2 = srt.slice_for_segment(cues, kept, speed=2.0)
    assert out2[1].start == pytest.approx(3.5)
    assert srt.map_time(106, kept) is None


def test_word_timings_and_karaoke_ass():
    words = srt.parse_word_timings("palabra,inicio,fin\nHabía,0.0,0.4\nuna,0.45,0.6\nvez,0.7,1.0\nfin,3.0,3.3\n")
    cues = srt.group_words(words, max_chars=20, max_gap=0.6)
    assert len(cues) == 2 and cues[0].text == "Había una vez"
    ass = srt.to_ass(cues, SubtitleStyle(highlight_color="#FFD400"), karaoke=True)
    assert "PlayResY: 1920" in ass and "{\\k40}Había" in ass and "&H0000D4FF" in ass


def test_ass_animation_and_wrap():
    ass = srt.to_ass([srt.Cue(0, 2, "una frase bastante larga para partir en dos")],
                     SubtitleStyle(animation="fade", max_chars=20))
    assert "\\fad(" in ass and "\\N" in ass
