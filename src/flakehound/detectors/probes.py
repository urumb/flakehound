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
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Probe:
    name: str
    label: str  # completes "fails ...", e.g. "on the last day of the month"
    kind: str  # "timezone", "clock" or "timing"
    env: dict[str, str] = field(default_factory=dict)
    clock: str | None = None
    sleep_scale: float | None = None
    switch_interval: float | None = None


def _next_leap_day(today: date) -> date:
    year = today.year
    while True:
        if calendar.isleap(year) and date(year, 2, 29) >= today:
            return date(year, 2, 29)
        year += 1


def _at(day: date, hh: int, mm: int, ss: int) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, ss).isoformat()


# Shared with diagnosis/timing.py so probe and confirmation test the same thing.
SLEEP_SCALE = 0.05

TIMING_PROBES = [
    Probe("short_waits", "when its sleeps are shorter", "timing", sleep_scale=SLEEP_SCALE),
    Probe("thread_stress", "when threads switch more often", "timing", switch_interval=1e-6),
]


def make_probes(today: date | None = None, *, timing: bool = True) -> list[Probe]:
    """Danger moments close to today (within ~4 years), so nothing else breaks
    for unrelated reasons like expired test certificates."""
    today = today or date.today()
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    probes = [
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
    return probes + (TIMING_PROBES if timing else [])


def posix_tz(offset: timedelta) -> str:
    """A POSIX TZ string for a fixed UTC offset (sign inverted: UTC+5:30 is ``FHT-5:30``)."""
    minutes = round(offset.total_seconds() / 60)
    sign = "-" if minutes > 0 else "+"
    hours, mins = divmod(abs(minutes), 60)
    return f"FHT{sign}{hours}" + (f":{mins:02d}" if mins else "")


def local_offset_probe(offset: timedelta | None = None) -> Probe:
    """Control for timezone findings: ``TZ`` set, but to this machine's own offset.

    If a test fails here too, it breaks because ``TZ`` is set (or because of its
    format), not because of the time difference.
    """
    if offset is None:
        offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return Probe(
        "tz_local_offset",
        "with TZ set to this machine's own UTC offset",
        "timezone",
        env={"TZ": posix_tz(offset)},
    )


def ordinary_day_probe(today: date | None = None) -> Probe:
    """Control for date findings: the clock shifted to an ordinary moment.

    Noon on the 15th of next month: no midnight, month end, year end or leap
    day. If a test fails here too, it breaks because the clock was shifted at
    all (often a clock read that flakehound cannot shift), not because of a date.
    """
    today = today or date.today()
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return Probe(
        "ordinary_day",
        "with the clock shifted to an ordinary day",
        "clock",
        clock=_at(date(year, month, 15), 12, 0, 0),
    )


def describe_clock(clock: str) -> str:
    dt = datetime.fromisoformat(clock)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["Probe", "describe_clock", "make_probes"]
