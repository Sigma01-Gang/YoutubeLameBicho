"""Cálculo de horarios de subida (funciones puras, sin base de datos)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta


@dataclass
class ChannelRules:
    channel_id: int
    slots: list[tuple[time, time]] = field(default_factory=list)
    min_gap: timedelta = timedelta(hours=2)
    daily_limit: int = 0  # 0 = sin límite
    rest_days: set[int] = field(default_factory=set)  # 0=lunes
    paused: bool = False


@dataclass
class Entry:
    id: int
    channel_id: int
    not_before: datetime | None = None  # p. ej. siguiente reintento


def parse_slot(text: str) -> tuple[time, time]:
    """'18:00-23:30' -> (18:00, 23:30)."""
    a, b = [s.strip() for s in text.split("-", 1)]
    ta = datetime.strptime(a, "%H:%M").time()
    tb = datetime.strptime(b, "%H:%M").time()
    return ta, tb


def in_slots(t: datetime, slots: list[tuple[time, time]]) -> bool:
    if not slots:
        return True
    tt = t.time()
    for a, b in slots:
        if a <= b and a <= tt <= b:
            return True
        if a > b and (tt >= a or tt <= b):  # franja que cruza medianoche
            return True
    return False


def next_slot_start(t: datetime, slots: list[tuple[time, time]]) -> datetime:
    """Primer instante >= t que cae en una franja preferida."""
    if in_slots(t, slots):
        return t
    for day in range(0, 8):
        base = (t + timedelta(days=day)).date()
        for a, _ in sorted(slots):
            cand = datetime.combine(base, a)
            if cand >= t:
                return cand
    return t


def _count_on_day(history: list[datetime], day) -> int:
    return sum(1 for h in history if h.date() == day)


def earliest_for(
    t: datetime,
    rules: ChannelRules,
    channel_history: list[datetime],
    max_iter: int = 500,
) -> datetime:
    """Ajusta t a la primera hora válida para el canal (franjas, descanso, límite diario, separación)."""
    for _ in range(max_iter):
        moved = False
        if channel_history:
            last = max(h for h in channel_history if h <= t) if any(h <= t for h in channel_history) else None
            if last is not None and t < last + rules.min_gap:
                t = last + rules.min_gap
                moved = True
            nxt = [h for h in channel_history if h > t]
            if nxt and min(nxt) - t < rules.min_gap:
                t = min(nxt) + rules.min_gap
                moved = True
        if t.weekday() in rules.rest_days:
            t = datetime.combine(t.date() + timedelta(days=1), time(0, 0))
            moved = True
            continue
        if rules.daily_limit and _count_on_day(channel_history, t.date()) >= rules.daily_limit:
            t = datetime.combine(t.date() + timedelta(days=1), time(0, 0))
            moved = True
            continue
        s = next_slot_start(t, rules.slots)
        if s != t:
            t = s
            moved = True
        if not moved:
            return t
    return t


def plan_schedule(
    entries: list[Entry],
    rules: dict[int, ChannelRules],
    history: list[tuple[int, datetime]],
    interval: timedelta,
    now: datetime,
    last_global: datetime | None = None,
    global_paused: bool = False,
) -> list[tuple[int, datetime | None]]:
    """Asigna una hora de subida a cada entrada respetando el orden de la cola.

    - `interval`: separación mínima entre dos subidas cualesquiera (p. ej. 3 h).
    - `history`: subidas ya realizadas (canal, hora), para respetar límites tras reinicios.
    - Las entradas de canales en pausa (o con pausa global) reciben None.
    """
    hist_by_ch: dict[int, list[datetime]] = {}
    for ch, t in history:
        hist_by_ch.setdefault(ch, []).append(t)
    all_hist = sorted(t for _, t in history)
    if last_global is None and all_hist:
        last_global = all_hist[-1]
    cursor = now if last_global is None else max(now, last_global + interval)
    out: list[tuple[int, datetime | None]] = []
    for e in entries:
        r = rules.get(e.channel_id, ChannelRules(e.channel_id))
        if global_paused or r.paused:
            out.append((e.id, None))
            continue
        t = cursor
        if e.not_before and e.not_before > t:
            t = e.not_before
        t = earliest_for(t, r, hist_by_ch.get(e.channel_id, []))
        out.append((e.id, t))
        hist_by_ch.setdefault(e.channel_id, []).append(t)
        cursor = t + interval
    return out


def backoff_delay(attempt: int, base_minutes: float = 5.0) -> timedelta:
    """Espera exponencial: base, 2·base, 4·base…"""
    return timedelta(minutes=base_minutes * (2 ** max(0, attempt - 1)))
