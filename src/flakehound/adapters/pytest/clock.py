"""A shifted wall clock: make "now" read a chosen date and time, then keep ticking.

Why shifted and not frozen: frozen time hangs code that waits for the clock to
move (polling loops, timeouts). A shifted clock moves normally, it just starts at
a different instant, like running the suite on another day.

What is shifted: ``time.time``/``time_ns``, ``time.localtime``/``gmtime``/
``ctime``/``asctime``/``strftime`` when called without an explicit time, and
``datetime.datetime.now/utcnow/today`` plus ``datetime.date.today``.
What is *not* shifted (on purpose): ``time.monotonic``, ``perf_counter`` and
``sleep``, so timeouts and durations stay real; file timestamps; C extensions.

Installed in the worker before tests are imported, so ``from datetime import
datetime`` in test and project code picks up the shifted class.
"""

from __future__ import annotations

import datetime as _dt
import sys
import time as _time
from collections.abc import Callable
from typing import Any

from flakehound.adapters.pytest.paths import canon, is_project_file

_real_datetime = _dt.datetime
_real_date = _dt.date
_real_time = _time.time
_real_time_ns = _time.time_ns
_real_localtime = _time.localtime
_real_gmtime = _time.gmtime
_real_ctime = _time.ctime
_real_asctime = _time.asctime
_real_strftime = _time.strftime

_offset = 0.0
_installed = False


def _now() -> float:
    return _real_time() + _offset


class _DatetimeMeta(type):
    def __instancecheck__(cls, obj: object) -> bool:
        return isinstance(obj, _real_datetime)

    def __subclasscheck__(cls, sub: type) -> bool:
        return issubclass(sub, _real_datetime)


class _DateMeta(type):
    def __instancecheck__(cls, obj: object) -> bool:
        return isinstance(obj, _real_date)

    def __subclasscheck__(cls, sub: type) -> bool:
        return issubclass(sub, _real_date)


class ShiftedDatetime(_real_datetime, metaclass=_DatetimeMeta):
    @classmethod
    def now(cls, tz: _dt.tzinfo | None = None) -> _dt.datetime:  # type: ignore[override]
        return _real_datetime.fromtimestamp(_now(), tz)

    @classmethod
    def utcnow(cls) -> _dt.datetime:  # type: ignore[override]
        return _real_datetime.fromtimestamp(_now(), _dt.timezone.utc).replace(tzinfo=None)

    @classmethod
    def today(cls) -> _dt.datetime:  # type: ignore[override]
        return _real_datetime.fromtimestamp(_now())


class ShiftedDate(_real_date, metaclass=_DateMeta):
    @classmethod
    def today(cls) -> _dt.date:  # type: ignore[override]
        return _real_date.fromtimestamp(_now())


def _wrap_optional_time(real: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(secs: float | None = None) -> Any:
        return real(_now() if secs is None else secs)

    wrapper.__name__ = getattr(real, "__name__", "wrapper")
    return wrapper


def _asctime(t: Any = None) -> str:
    return _real_asctime(_real_localtime(_now()) if t is None else t)


def _strftime(fmt: str, t: Any = None) -> str:
    return _real_strftime(fmt, _real_localtime(_now()) if t is None else t)


def parse_target(text: str) -> _dt.datetime:
    """``2026-12-31T23:59:58`` (local wall time) -> naive datetime."""
    return _real_datetime.fromisoformat(text).replace(tzinfo=None)


def install(target: str, project_root: str | None = None) -> None:
    """Shift the clock so local wall time reads ``target`` right now."""
    global _offset, _installed
    wanted = parse_target(target).timestamp()  # naive -> interpreted as local time
    _offset = wanted - _real_time()
    if _installed:
        return
    _installed = True
    _time.time = _now
    _time.time_ns = lambda: _real_time_ns() + int(_offset * 1e9)
    _time.localtime = _wrap_optional_time(_real_localtime)
    _time.gmtime = _wrap_optional_time(_real_gmtime)
    _time.ctime = _wrap_optional_time(_real_ctime)
    _time.asctime = _asctime
    _time.strftime = _strftime
    _dt.datetime = ShiftedDatetime  # type: ignore[misc]
    _dt.date = ShiftedDate  # type: ignore[misc]
    if project_root:
        _rebind_project_modules(project_root)


def _rebind_project_modules(project_root: str) -> None:
    """Replace ``from datetime import datetime`` bindings in already-imported
    project modules (e.g. conftest.py). Library modules are left alone."""
    root = canon(project_root)
    swap = {id(_real_datetime): ShiftedDatetime, id(_real_date): ShiftedDate}
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if not file or not is_project_file(file, root):
            continue
        for name, value in list(vars(module).items()):
            replacement = swap.get(id(value))
            if replacement is not None:
                setattr(module, name, replacement)
