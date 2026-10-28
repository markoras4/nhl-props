"""When a game stops changing on the site: 5 minutes before its scheduled start, or as soon as the NHL feed shows it
under way, whichever comes first. The NHL feed can keep a game as "future" well after the scheduled start, and
sportsbooks switch to live in-game odds at puck drop, so the scheduled time is the safer clock."""
from __future__ import annotations

import datetime as dt

LOCK_MINUTES = 5
PRE_STATES = (None, "FUT", "PRE")


def start_of(g: dict) -> dt.datetime | None:
    s = g.get("start")
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_locked(g: dict, now: dt.datetime | None = None) -> bool:
    if g.get("state") not in PRE_STATES:
        return True
    st = start_of(g)
    now = now or dt.datetime.now(dt.timezone.utc)
    return st is not None and now >= st - dt.timedelta(minutes=LOCK_MINUTES)


def too_late(start_iso: str | None, now: dt.datetime | None = None) -> bool:
    """For outside feeds (sportsbooks): is this start time within the lock window or past it?"""
    if not start_iso:
        return False
    try:
        st = dt.datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
    except ValueError:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    return now >= st - dt.timedelta(minutes=LOCK_MINUTES)
