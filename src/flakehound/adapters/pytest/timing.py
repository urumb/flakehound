"""Timing stress inside a worker: shorter waits and frequent thread switches.

* **Sleep shrinking.** ``time.sleep`` is wrapped. Sleeps made on the *main
  thread* (where the test waits) can be scaled down, either all of them or only
  the ones at given call sites (``path:line``). Sleeps in background threads
  (which usually simulate work) are left alone. If a test fails when its wait
  is shortened, it was using ``sleep`` to wait for something that is not
  guaranteed to be done: on a slower machine it will fail the same way.
* **Call-site recording.** Every main-thread sleep is attributed to the first
  stack frame in the project's own code, so we can say *which* sleep matters.
* **Thread-switch stress.** ``sys.setswitchinterval`` makes the interpreter
  switch threads far more often, exposing races that are rare by default.

``time.monotonic``, ``perf_counter`` and ``asyncio.sleep`` are not touched.
"""

from __future__ import annotations

import os
import sys
import sysconfig
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import FrameType

_real_sleep: Callable[[float], None] = _time.sleep
# flakehound's own package dir: never attribute sleeps to our code.
_OWN_PACKAGE = (
    os.path.normcase(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) + os.sep
)


@dataclass
class SleepSite:
    site: str  # "tests/test_upload.py:9"
    seconds: float  # longest requested sleep at this site
    count: int = 0

    def to_json(self) -> dict[str, object]:
        return {"site": self.site, "seconds": round(self.seconds, 6), "count": self.count}


@dataclass
class _State:
    root: str = ""
    scale: float | None = None
    only_sites: set[str] | None = None
    current_test: str | None = None
    sites: dict[str, dict[str, SleepSite]] = field(default_factory=dict)  # test -> site -> info
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _State()
_libs: list[str] = []


def _library_paths() -> list[str]:
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    known = sysconfig.get_paths()
    for key in ("purelib", "platlib", "stdlib", "platstdlib"):
        if key in known:
            paths.add(known[key])
    return [os.path.normcase(os.path.abspath(p)) for p in paths if p]


def _project_site(frame: FrameType | None) -> str | None:
    """``relative/path.py:line`` of the first frame in the project's own code."""
    while frame is not None:
        path = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        in_project = path.startswith(_state.root) and not any(path.startswith(x) for x in _libs)
        if in_project and not path.startswith(_OWN_PACKAGE):
            rel = os.path.relpath(frame.f_code.co_filename, _state.root)
            return f"{rel.replace(os.sep, '/')}:{frame.f_lineno}"
        frame = frame.f_back
    return None


def _sleep(seconds: float) -> None:
    if threading.current_thread() is threading.main_thread() and _state.current_test:
        site = _project_site(sys._getframe(1))
        if site is not None:
            with _state.lock:
                per_test = _state.sites.setdefault(_state.current_test, {})
                info = per_test.setdefault(site, SleepSite(site, 0.0))
                info.count += 1
                info.seconds = max(info.seconds, float(seconds))
            if _state.scale is not None and (
                _state.only_sites is None or site in _state.only_sites
            ):
                seconds = seconds * _state.scale
    _real_sleep(seconds)


def install(
    root: str,
    *,
    scale: float | None = None,
    only_sites: list[str] | None = None,
    switch_interval: float | None = None,
) -> None:
    """Patch ``time.sleep`` (recording always; shrinking if ``scale``)."""
    _state.root = os.path.normcase(os.path.abspath(root))
    _state.scale = scale
    _state.only_sites = set(only_sites) if only_sites else None
    _libs[:] = _library_paths()
    if _time.sleep is not _sleep:
        _time.sleep = _sleep  # type: ignore[assignment]
        _rebind_project_modules()
    if switch_interval is not None:
        sys.setswitchinterval(switch_interval)


def _rebind_project_modules() -> None:
    """``from time import sleep`` in already-imported project modules (e.g. conftest)."""
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if not file:
            continue
        path = os.path.normcase(os.path.abspath(file))
        if not path.startswith(_state.root) or any(path.startswith(lib) for lib in _libs):
            continue
        for name, value in list(vars(module).items()):
            if value is _real_sleep:
                setattr(module, name, _sleep)


def set_current_test(nodeid: str | None) -> None:
    _state.current_test = nodeid


def sites_for(nodeid: str) -> list[SleepSite]:
    with _state.lock:
        return sorted(_state.sites.get(nodeid, {}).values(), key=lambda s: s.site)
