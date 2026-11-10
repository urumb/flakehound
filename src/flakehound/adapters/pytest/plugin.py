"""pytest plugin loaded *inside* each worker process (``-p flakehound.adapters.pytest.plugin``).

It is deliberately not registered as an auto-loaded ``pytest11`` entry point:
installing flakehound must never change how your normal ``pytest`` runs behave.

What it does, only when flakehound passes its options:
* keeps exactly the tests in the run spec, in the spec's order;
* seeds ``random`` per test from the spec seed (deterministic and independent of order);
* writes one JSON result per test (redacted failure message included);
* snapshots global state around the tests listed in ``spec.snapshot``;
* shifts the wall clock when the spec asks for it;
* shortens main-thread sleeps / stresses thread switching when asked (timing.py).

Two options are also meant for humans, to replay what flakehound found with
plain pytest: ``--flakehound-seed=N`` (seed ``random`` per test exactly like a
flakehound run) and ``--flakehound-clock=2026-12-31T23:59:58`` (shift the clock).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from flakehound.adapters.pytest import clock, snapshot, timing
from flakehound.core.models import Outcome, RunSpec, StateChange, TestResult
from flakehound.report.redact import redact, truncate

_MESSAGE_LINES = 25


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("flakehound", "flakehound worker (internal)")
    group.addoption("--flakehound-spec", default=None, help="internal: run spec JSON")
    group.addoption("--flakehound-out", default=None, help="internal: results JSON")
    group.addoption("--flakehound-collect-out", default=None, help="internal: collected ids JSON")
    group.addoption(
        "--flakehound-seed",
        type=int,
        default=None,
        help="seed `random` per test like flakehound does (to replay a finding)",
    )
    group.addoption(
        "--flakehound-clock",
        default=None,
        metavar="ISO_DATETIME",
        help="make the wall clock start at this local time, e.g. 2026-12-31T23:59:58",
    )
    group.addoption(
        "--flakehound-sleep-scale",
        type=float,
        default=None,
        metavar="FACTOR",
        help="shorten time.sleep() calls made by tests on the main thread by this factor",
    )
    group.addoption(
        "--flakehound-sleep-sites",
        default=None,
        metavar="PATH:LINE,...",
        help="only shorten sleeps called from these source lines",
    )
    group.addoption(
        "--flakehound-switch-interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="thread switch interval (e.g. 1e-6) to expose race conditions",
    )


class _Worker:
    def __init__(
        self,
        spec: RunSpec | None,
        out: Path | None,
        collect_out: Path | None,
        seed: int | None = None,
    ) -> None:
        self.spec = spec
        self.seed = spec.seed if spec is not None else seed
        self.out = out
        self.collect_out = collect_out
        self.results: dict[str, TestResult] = {}
        self.state_changes: dict[str, list[StateChange]] = {}

    # --- ordering ---------------------------------------------------------
    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        if self.spec is None:
            return
        position = {nodeid: i for i, nodeid in enumerate(self.spec.order)}
        keep = [it for it in items if it.nodeid in position]
        drop = [it for it in items if it.nodeid not in position]
        keep.sort(key=lambda it: position[it.nodeid])
        if drop:
            config.hook.pytest_deselected(items=drop)
        items[:] = keep

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if self.collect_out is not None:
            _write_json(self.collect_out, [item.nodeid for item in session.items])

    # --- determinism ------------------------------------------------------
    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        if self.seed is not None:
            seed_for_test(self.seed, item.nodeid)

    # --- state snapshots ----------------------------------------------------
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(
        self, item: pytest.Item, nextitem: pytest.Item | None
    ) -> Generator[None, None, None]:
        timing.set_current_test(item.nodeid)
        try:
            watch = self.spec is not None and item.nodeid in self.spec.snapshot
            if not watch:
                yield
                return
            root = Path(str(item.config.rootpath))
            before = snapshot.take(root)
            yield  # setup, call and teardown of this test
            after = snapshot.take(root)
            self.state_changes[item.nodeid] = snapshot.diff(before, after)
        finally:
            timing.set_current_test(None)

    # --- results ----------------------------------------------------------
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if self.spec is None:
            return
        outcome = _phase_outcome(report)
        if outcome is None:
            return
        previous = self.results.get(report.nodeid)
        message = ""
        if outcome.is_failure:
            message = _short_message(report)
        if previous is None:
            self.results[report.nodeid] = TestResult(
                report.nodeid, outcome, report.duration, message
            )
            return
        # Combine phases: any failure wins; durations add up.
        merged = previous.outcome
        if outcome.is_failure and not previous.outcome.is_failure:
            merged = outcome
        self.results[report.nodeid] = TestResult(
            report.nodeid,
            merged,
            previous.duration + report.duration,
            previous.message or message,
        )

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.spec is None or self.out is None:
            return
        order = {nodeid: i for i, nodeid in enumerate(self.spec.order)}
        ordered = sorted(self.results.values(), key=lambda r: order.get(r.nodeid, len(order)))
        _write_json(
            self.out,
            {
                "results": [r.to_json() for r in ordered],
                "state_changes": {
                    nodeid: [c.to_json() for c in changes]
                    for nodeid, changes in self.state_changes.items()
                },
                "sleep_sites": {
                    r.nodeid: [s.to_json() for s in timing.sites_for(r.nodeid)]
                    for r in ordered
                    if timing.sites_for(r.nodeid)
                },
            },
        )


def seed_for_test(seed: int, nodeid: str) -> None:
    """Seed the global RNGs for one test, independent of test order.

    String seeds are hashed with SHA-512 by `random`, so this is stable across
    processes and PYTHONHASHSEED values.
    """
    key = f"{seed}:{nodeid}"
    random.seed(key)
    numpy = sys.modules.get("numpy")  # only if the project already uses it
    if numpy is not None:
        digest = hashlib.sha256(key.encode()).digest()
        with contextlib.suppress(Exception):
            numpy.random.seed(int.from_bytes(digest[:4], "big"))


def _phase_outcome(report: pytest.TestReport) -> Outcome | None:
    if report.when == "call":
        if report.passed:
            return Outcome.PASSED
        if report.skipped:  # includes xfail
            return Outcome.SKIPPED
        return Outcome.FAILED
    # setup / teardown
    if report.failed:
        return Outcome.ERROR
    if report.skipped and report.when == "setup":
        return Outcome.SKIPPED
    return None


def _short_message(report: pytest.TestReport) -> str:
    text = report.longreprtext or ""
    lines = text.strip().splitlines()[-_MESSAGE_LINES:]
    return truncate(redact("\n".join(lines)))


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def pytest_configure(config: pytest.Config) -> None:
    spec_path = config.getoption("flakehound_spec")
    out_path = config.getoption("flakehound_out")
    collect_path = config.getoption("flakehound_collect_out")
    user_seed = config.getoption("flakehound_seed")
    user_clock = config.getoption("flakehound_clock")
    user_scale = config.getoption("flakehound_sleep_scale")
    user_sites = config.getoption("flakehound_sleep_sites")
    user_switch = config.getoption("flakehound_switch_interval")
    user_timing = user_scale is not None or user_switch is not None
    if not (spec_path or collect_path or user_seed is not None or user_clock or user_timing):
        return
    spec = None
    if spec_path:
        spec = RunSpec.from_json(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    target_clock = spec.clock if spec is not None else user_clock
    if target_clock:
        try:
            clock.install(target_clock, str(config.rootpath))
        except ValueError as exc:
            raise pytest.UsageError(f"--flakehound-clock: {exc}") from exc
    if spec is not None and spec.stresses_timing:
        timing.install(
            str(config.rootpath),
            scale=spec.sleep_scale,
            only_sites=spec.sleep_sites or None,
            switch_interval=spec.switch_interval,
        )
    elif spec is None and user_timing:
        if user_scale is not None and not 0 < user_scale <= 1:
            raise pytest.UsageError("--flakehound-sleep-scale must be in (0, 1]")
        if user_switch is not None and user_switch <= 0:
            raise pytest.UsageError("--flakehound-switch-interval must be positive")
        timing.install(
            str(config.rootpath),
            scale=user_scale,
            only_sites=[x for x in (user_sites or "").split(",") if x] or None,
            switch_interval=user_switch,
        )
    config.pluginmanager.register(
        _Worker(
            spec,
            Path(out_path) if out_path else None,
            Path(collect_path) if collect_path else None,
            seed=user_seed,
        ),
        "flakehound-worker",
    )
