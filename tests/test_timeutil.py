import pytest
from datetime import datetime
from shortsfactory.core.timeutil import (format_timestamp, parse_datetime, parse_timestamp, snap_to_frame,
                                         step_frames)


def test_parse_timestamp_variants():
    assert parse_timestamp("00:01:30") == 90
    assert parse_timestamp("1:02:03.5") == 3723.5
    assert parse_timestamp("02:03") == 123
    assert parse_timestamp("12.25") == 12.25
    assert parse_timestamp(7) == 7.0
    with pytest.raises(ValueError):
        parse_timestamp("abc")


def test_format_timestamp():
    assert format_timestamp(3723.5, ",") == "01:02:03,500"
    assert format_timestamp(-1) == "00:00:00.000"


def test_frame_stepping():
    assert step_frames(1.0, 1, 30) == pytest.approx(31 / 30)
    assert step_frames(1.0, -30, 30) == pytest.approx(0.0)
    assert snap_to_frame(1.01, 30) == pytest.approx(30 / 30)


def test_parse_datetime():
    assert parse_datetime("2026-12-01 18:00") == datetime(2026, 12, 1, 18, 0)
    assert parse_datetime("01/12/2026 18:30") == datetime(2026, 12, 1, 18, 30)
    assert parse_datetime("") is None
    with pytest.raises(ValueError):
        parse_datetime("mañana")
