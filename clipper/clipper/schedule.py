"""Assign clips to posting slots."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Schedule


def next_slots(cfg: Schedule, now: datetime, taken: set[str], count: int) -> list[datetime]:
    """The next free slots, in order, up to `max_days_ahead` days out.

    `now` must be timezone-aware. Slots already in `taken` (ISO strings) and
    slots closer than `min_lead_minutes` are skipped.
    """
    tz = ZoneInfo(cfg.timezone)
    local = now.astimezone(tz)
    earliest = local + timedelta(minutes=cfg.min_lead_minutes)
    out: list[datetime] = []
    for day in range(cfg.max_days_ahead + 1):
        date = (local + timedelta(days=day)).date()
        for slot in sorted(cfg.slots):
            hh, mm = (int(x) for x in slot.split(":"))
            dt = datetime(date.year, date.month, date.day, hh, mm, tzinfo=tz)
            if dt < earliest or dt.isoformat() in taken:
                continue
            out.append(dt)
            if len(out) == count:
                return out
    return out
