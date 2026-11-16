from shortsfactory.ffmpeg.runner import MediaInfo
from shortsfactory.review.checks import evaluate, parse_blackdetect, parse_freezedetect, parse_volumedetect

STDERR = """
[blackdetect @ 0x1] black_start:0 black_end:1.2 black_duration:1.2
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 10.0
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 14.0
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 58.8
[Parsed_volumedetect_0 @ 0x3] mean_volume: -40.2 dB
[Parsed_volumedetect_0 @ 0x3] max_volume: 0.0 dB
"""


def test_parsers():
    assert parse_volumedetect(STDERR) == (0.0, -40.2)
    assert parse_blackdetect(STDERR) == [(0.0, 1.2)]
    assert parse_freezedetect(STDERR, 60) == [(10.0, 14.0), (58.8, 60)]


def test_evaluate_short():
    info = MediaInfo(61.0, 1080, 1920, 30, True, 6_000_000, "h264", "aac")
    rep = evaluate(info, STDERR, is_short=True, freeze_tail=2.0)
    msgs = " | ".join(i.message for i in rep.issues)
    assert not rep.ok and "supera 60" in msgs
    assert "saturado" in msgs and "demasiado bajo" in msgs and "negros" in msgs
    assert "10.0s a 14.0s" in msgs and "58.8" not in msgs  # congelado final intencionado ignorado


def test_evaluate_ok_and_resolution():
    good = MediaInfo(45.0, 1080, 1920, 30, True, 6_000_000, "h264", "aac")
    assert evaluate(good, "", is_short=True).ok
    bad = MediaInfo(45.0, 720, 1280, 30, True, 6_000_000, "h264", "aac")
    assert not evaluate(bad, "", is_short=True).ok
