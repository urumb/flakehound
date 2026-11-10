"""Environment probes: run the suite as if it were a different place or day.

Each probe is a full run with the *same order and seeds* as the baseline run
(run 0), with exactly one thing changed: the timezone, or the wall clock set to
a "danger" moment. A test that passes in the baseline but fails under a probe is
a time bomb: it passes today and will fail on that day, or on a CI machine in
that timezone.

Timezones use POSIX TZ strings with a fixed offset (no timezone database needed,
works on Linux, macOS and Windows). Note the inverted sign: ``FHT-14`` is UTC+14.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Probe:
    name: str
    label: str  # completes "fails ...", e.g. "on the last day of the month"
    kind: str  # "timezone" or "clock"
    env: dict[str, str] = field(default_factory=dict)
    clock: str | None = None


def _next_leap_day(today: date) -> date:
    year = today.year
    while True:
        if calendar.isleap(year) and date(year, 2, 29) >= today:
            return date(year, 2, 29)
        year += 1


def _at(day: date, hh: int, mm: int, ss: int) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, ss).isoformat()


def make_probes(today: date | None = None) -> list[Probe]:
    """Danger moments close to today (within ~4 years), so nothing else breaks
    for unrelated reasons like expired test certificates."""
    today = today or date.today()
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    return [
        Probe("tz_utc_plus_14", "in timezone UTC+14", "timezone", env={"TZ": "FHT-14"}),
        Probe("tz_utc_minus_12", "in timezone UTC-12", "timezone", env={"TZ": "FHT+12"}),
        Probe("midnight", "two seconds before midnight", "clock", clock=_at(today, 23, 59, 58)),
        Probe("month_end", "on the last day of the month", "clock", clock=_at(last_day, 12, 0, 0)),
        Probe(
            "year_end",
            "on New Year's Eve, two seconds before midnight",
            "clock",
            clock=_at(date(today.year, 12, 31), 23, 59, 58),
        ),
        Probe(
            "leap_day",
            "on a leap day (Feb 29)",
            "clock",
            clock=_at(_next_leap_day(today), 12, 0, 0),
        ),
    ]


def describe_clock(clock: str) -> str:
    dt = datetime.fromisoformat(clock)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["Probe", "describe_clock", "make_probes"]
