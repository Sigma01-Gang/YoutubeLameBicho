from datetime import datetime, time, timedelta

from shortsfactory.scheduling.scheduler import (ChannelRules, Entry, backoff_delay, in_slots, parse_slot,
                                                plan_schedule)

NOW = datetime(2026, 11, 16, 10, 0)  # lunes


def test_global_interval_is_respected():
    entries = [Entry(i, 1) for i in range(1, 4)]
    plan = plan_schedule(entries, {1: ChannelRules(1, min_gap=timedelta(0))}, [], timedelta(hours=3), NOW)
    times = [t for _, t in plan]
    assert times == [NOW, NOW + timedelta(hours=3), NOW + timedelta(hours=6)]


def test_interval_after_restart_uses_last_upload():
    last = NOW - timedelta(hours=1)
    plan = plan_schedule([Entry(1, 1)], {}, [(1, last)], timedelta(hours=3), NOW)
    assert plan[0][1] == last + timedelta(hours=3)


def test_preferred_slots_and_min_gap_per_channel():
    rules = {1: ChannelRules(1, slots=[parse_slot("18:00-23:00")], min_gap=timedelta(hours=4)),
             2: ChannelRules(2, min_gap=timedelta(0))}
    plan = plan_schedule([Entry(1, 1), Entry(2, 2), Entry(3, 1)], rules, [], timedelta(hours=1), NOW)
    t1, t2, t3 = [t for _, t in plan]
    assert t1 == datetime(2026, 11, 16, 18, 0)
    assert t2 == t1 + timedelta(hours=1)
    assert t3 >= t1 + timedelta(hours=4) and in_slots(t3, rules[1].slots)


def test_daily_limit_and_rest_days():
    rules = {1: ChannelRules(1, min_gap=timedelta(0), daily_limit=2, rest_days={1})}  # martes descanso
    plan = plan_schedule([Entry(i, 1) for i in range(4)], rules, [], timedelta(hours=1), NOW)
    days = [t.date() for _, t in plan]
    assert days.count(NOW.date()) == 2
    assert all(t.weekday() != 1 for _, t in plan)
    assert plan[2][1].date() == datetime(2026, 11, 18).date()


def test_paused_channel_and_global_pause():
    rules = {1: ChannelRules(1, paused=True), 2: ChannelRules(2)}
    plan = plan_schedule([Entry(1, 1), Entry(2, 2)], rules, [], timedelta(hours=3), NOW)
    assert plan[0][1] is None and plan[1][1] == NOW
    plan = plan_schedule([Entry(2, 2)], rules, [], timedelta(hours=3), NOW, global_paused=True)
    assert plan[0][1] is None


def test_slot_crossing_midnight_and_backoff():
    s = [parse_slot("22:00-02:00")]
    assert in_slots(datetime(2026, 1, 1, 1, 0), s) and not in_slots(datetime(2026, 1, 1, 12, 0), s)
    assert [backoff_delay(i, 5) for i in (1, 2, 3)] == [timedelta(minutes=5), timedelta(minutes=10),
                                                         timedelta(minutes=20)]
