"""pytest plugin loaded *inside* each worker process (``-p flakehound.adapters.pytest.plugin``).

It is deliberately not registered as an auto-loaded ``pytest11`` entry point:
installing flakehound must never change how your normal ``pytest`` runs behave.

What it does, only when flakehound passes its options:
* keeps exactly the tests in the run spec, in the spec's order;
* seeds ``random`` per test from the spec seed (deterministic and independent of order);
* writes one JSON result per test (redacted failure message included);
* snapshots global state around the tests listed in ``spec.snapshot``.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from flakehound.adapters.pytest import snapshot
from flakehound.core.models import Outcome, RunSpec, StateChange, TestResult
from flakehound.report.redact import redact, truncate

_MESSAGE_LINES = 25


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("flakehound", "flakehound worker (internal)")
    group.addoption("--flakehound-spec", default=None, help="internal: run spec JSON")
    group.addoption("--flakehound-out", default=None, help="internal: results JSON")
    group.addoption("--flakehound-collect-out", default=None, help="internal: collected ids JSON")


class _Worker:
    def __init__(self, spec: RunSpec | None, out: Path | None, collect_out: Path | None) -> None:
        self.spec = spec
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
        if self.spec is not None:
            # String seeds are hashed with SHA-512 by `random`, so this is
            # stable across processes and PYTHONHASHSEED values.
            random.seed(f"{self.spec.seed}:{item.nodeid}")

    # --- state snapshots ----------------------------------------------------
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(
        self, item: pytest.Item, nextitem: pytest.Item | None
    ) -> Generator[None, None, None]:
        watch = self.spec is not None and item.nodeid in self.spec.snapshot
        if not watch:
            yield
            return
        root = Path(str(item.config.rootpath))
        before = snapshot.take(root)
        yield  # setup, call and teardown of this test
        after = snapshot.take(root)
        self.state_changes[item.nodeid] = snapshot.diff(before, after)

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
            },
        )


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
    if not (spec_path or collect_path):
        return
    spec = None
    if spec_path:
        spec = RunSpec.from_json(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    config.pluginmanager.register(
        _Worker(
            spec,
            Path(out_path) if out_path else None,
            Path(collect_path) if collect_path else None,
        ),
        "flakehound-worker",
    )
