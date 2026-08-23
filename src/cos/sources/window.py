"""Time-window resolution.

The default is the last 24 hours, extended backwards across weekends and holidays so a
Monday run reaches Friday evening. A fixed 24 hours silently drops the weekend, which is
exactly when a Monday-morning brief matters most. See `docs/decisions.md` D-008.

Pure functions. No clock reads inside — `now` is always passed, so every window is
reproducible in a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from cos.settings import WindowSettings

# Statutory holidays are deliberately not modelled. A wrong holiday table silently
# changes the window, and the failure is invisible; a weekend rule is right every week.
# If a holiday matters, `cos brief --hours N` overrides it explicitly.


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    calendar_start: datetime
    calendar_end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


def is_working_day(day: date) -> bool:
    return day.weekday() < 5


def add_business_days(start: date, count: int) -> date:
    """`count` business days after `start`, not counting `start` itself."""
    day = start
    remaining = count
    while remaining > 0:
        day += timedelta(days=1)
        if is_working_day(day):
            remaining -= 1
    return day


def resolve(
    settings: WindowSettings,
    *,
    now: datetime,
    lookback_hours: int | None = None,
) -> Window:
    """Resolve the mail/chat window and the calendar look-ahead.

    An explicit `lookback_hours` is taken literally and never extended — if the operator
    asked for six hours, they meant six hours.
    """
    tz = ZoneInfo(settings.timezone)
    local_now = now.astimezone(tz)

    explicit = lookback_hours is not None
    hours: int = lookback_hours if lookback_hours is not None else settings.lookback_hours
    start = local_now - timedelta(hours=hours)

    if not explicit and settings.extend_over_non_working_days:
        # If the window would begin on a weekend, walk back to the most recent working
        # day and take all of it. A Monday-morning run must reach Friday, not Sunday.
        while not is_working_day(start.date()):
            start = datetime.combine(start.date() - timedelta(days=1), time(0, 0), tzinfo=tz)

    calendar_start = datetime.combine(local_now.date(), time(0, 0), tzinfo=tz)
    last_day = add_business_days(local_now.date(), settings.calendar_lookahead_business_days)
    calendar_end = datetime.combine(last_day + timedelta(days=1), time(0, 0), tzinfo=tz)

    return Window(
        start=start,
        end=local_now,
        calendar_start=calendar_start,
        calendar_end=calendar_end,
    )
