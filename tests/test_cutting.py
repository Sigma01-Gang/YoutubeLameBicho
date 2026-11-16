import pytest
from shortsfactory.core.models import Segment
from shortsfactory.ingest import cutting as c


def test_20_minutes_gives_7_parts_of_3_minutes():
    segs = c.split_fixed(20 * 60, 180)
    assert len(segs) == 7
    assert all(s.duration == pytest.approx(180) for s in segs[:6])
    assert segs[-1].duration == pytest.approx(120)
    assert segs[0].label == "Parte 1/7" and segs[-1].label == "Parte 7/7"
    # contiguos y sin huecos
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start


def test_split_fixed_merges_short_tail():
    segs = c.split_fixed(370, 180, min_tail=30)
    assert len(segs) == 2 and segs[-1].end == 370


def test_parse_silencedetect():
    err = ("[silencedetect @ 0x1] silence_start: 10.5\n"
           "[silencedetect @ 0x1] silence_end: 12.0 | silence_duration: 1.5\n"
           "[silencedetect @ 0x1] silence_start: 50\n")
    assert c.parse_silencedetect(err, total=60) == [(10.5, 12.0), (50.0, 60.0)]


def test_split_clean_snaps_to_nearest_silence():
    silences = [(175.0, 177.0), (400.0, 402.0)]
    segs = c.split_clean(600, 180, silences, tolerance=8)
    assert segs[0].end == pytest.approx(176.0)
    assert segs[1].start == pytest.approx(176.0)
    # el siguiente corte (176+180=356) no tiene silencio cerca: se queda en el objetivo
    assert segs[1].end == pytest.approx(356.0)


def test_split_shorts_never_exceed_60():
    segs = c.split_shorts(500, 45, silences=[(44, 45.5), (95, 96)])
    assert segs and all(s.duration <= 60 for s in segs)
    assert all(s.kind == "short" for s in segs)


def test_chapters_txt():
    txt = "00:00:00 Intro\n00:03:10 El sótano\n0:07:00 - Final\nbasura\n"
    segs = c.parse_chapters_txt(txt, total=600)
    assert [s.label for s in segs] == ["Intro", "El sótano", "Final"]
    assert segs[1].start == 190 and segs[1].end == 420 and segs[2].end == 600


def test_marks_csv():
    segs = c.parse_marks_csv("inicio,fin,titulo\n00:00:05,00:00:50,Gancho\n60,100,\n")
    assert segs[0].start == 5 and segs[0].end == 50 and segs[0].label == "Gancho"
    assert segs[1].duration == 40


def test_fit_to_duration_and_duplicate():
    s = Segment(10, 80)
    assert c.fit_to_duration(s, 59).duration == pytest.approx(59)
    assert c.fit_to_duration(s, 59, "both").start == pytest.approx(15.5)
    d = c.duplicate_with_duration(s, 30)
    assert d.start == 10 and d.end == 40


def test_remove_pauses_and_output_duration():
    s = c.remove_pauses(Segment(0, 60), [(10, 13), (30, 30.5), (40, 45)], threshold=1.2, keep_pad=0.25)
    assert s.keep == [(0, 10.25), (12.75, 40.25), (44.75, 60)]
    assert s.output_duration() == pytest.approx(60 - 2.5 - 4.5)
    assert Segment(0, 10, speed=2.0).output_duration() == pytest.approx(5)


def test_trim_edge_silence():
    s = c.trim_edge_silence(Segment(0, 30), [(0, 2), (28, 30)], pad=0.1)
    assert s.start == pytest.approx(1.9) and s.end == pytest.approx(28.1)


def test_scene_candidates_and_ranking():
    cands = c.candidates_from_scenes([10, 30, 50, 80], 100, 15, 60)
    assert all(15 <= x.duration <= 60 for x in cands)
    loud = [(t, 1.0 if 30 <= t < 80 else 0.1) for t in range(100)]
    motion = [(t, 0.5) for t in range(100)]
    ranked = c.score_candidates(cands, loud, motion, ideal=45)
    assert ranked[0].start >= 30 and ranked[0].end <= 80
    assert ranked[0].score >= ranked[-1].score


def test_fit_to_duration_accounts_for_pauses_and_speed():
    s = Segment(0, 80, keep=[(0, 20), (30, 80)])  # 70 s útiles
    f = c.fit_to_duration(s, 59)
    assert f.output_duration() == pytest.approx(59) and f.keep[0] == (0, 20)
    fast = c.fit_to_duration(Segment(0, 100, speed=1.5), 59)
    assert fast.output_duration() == pytest.approx(59)
    both = c.fit_to_duration(Segment(0, 70), 50, "both")
    assert both.start == pytest.approx(10) and both.end == pytest.approx(60)
