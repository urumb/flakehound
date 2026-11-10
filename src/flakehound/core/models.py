"""Runner-agnostic data model.

Everything that crosses a process boundary (run specs, results) is a plain
dataclass that converts to/from JSON, so any test runner adapter can produce it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"  # the test body failed
    ERROR = "error"  # setup/teardown failed
    SKIPPED = "skipped"

    @property
    def is_failure(self) -> bool:
        return self in (Outcome.FAILED, Outcome.ERROR)


@dataclass(frozen=True)
class StateChange:
    """One piece of global state a test changed and did not restore."""

    kind: str  # "global", "class_attr", "env", "cwd", "file", "sys_path", "logging"
    name: str  # e.g. "shop.cart", "APP_CURRENCY", "data/out.csv"
    before: str  # short, redacted representation ("<missing>" when it didn't exist)
    after: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StateChange:
        return cls(
            kind=str(data["kind"]),
            name=str(data["name"]),
            before=str(data["before"]),
            after=str(data["after"]),
        )


@dataclass(frozen=True)
class Repro:
    """How a user can reproduce a finding with their normal test runner."""

    order: list[str]  # tests to run, in this order; the last one is the one that fails
    env: dict[str, str] = field(default_factory=dict)  # e.g. {"TZ": "FHT-14"}
    seed: int | None = None  # replay flakehound's per-test random seed
    clock: str | None = None  # shifted wall clock start
    sleep_scale: float | None = None  # shorten main-thread sleeps by this factor
    sleep_sites: list[str] = field(default_factory=list)  # ...only at these call sites
    switch_interval: float | None = None  # thread-switch stress

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunSpec:
    """One experiment: run these tests, in this order, under these conditions."""

    run_id: int
    order: list[str]
    seed: int
    hash_seed: int | None = None
    env: dict[str, str] = field(default_factory=dict)
    label: str = ""
    # Tests to snapshot global state around (before setup, after teardown).
    snapshot: list[str] = field(default_factory=list)
    # Local wall time the clock should read at start, e.g. "2026-12-31T23:59:58".
    clock: str | None = None
    # Timing stress (see adapters/pytest/timing.py).
    sleep_scale: float | None = None
    sleep_sites: list[str] = field(default_factory=list)
    switch_interval: float | None = None
    record_sleeps: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunSpec:
        return cls(
            run_id=int(data["run_id"]),
            order=[str(x) for x in data["order"]],
            seed=int(data["seed"]),
            hash_seed=None if data.get("hash_seed") is None else int(data["hash_seed"]),
            env={str(k): str(v) for k, v in data.get("env", {}).items()},
            label=str(data.get("label", "")),
            snapshot=[str(x) for x in data.get("snapshot", [])],
            clock=None if data.get("clock") is None else str(data["clock"]),
            sleep_scale=None if data.get("sleep_scale") is None else float(data["sleep_scale"]),
            sleep_sites=[str(x) for x in data.get("sleep_sites", [])],
            switch_interval=(
                None if data.get("switch_interval") is None else float(data["switch_interval"])
            ),
            record_sleeps=bool(data.get("record_sleeps", False)),
        )

    @property
    def stresses_timing(self) -> bool:
        return (
            self.sleep_scale is not None or self.switch_interval is not None or self.record_sleeps
        )


@dataclass(frozen=True)
class TestResult:
    nodeid: str
    outcome: Outcome
    duration: float
    message: str = ""  # short, redacted failure summary

    __test__ = False  # stop pytest from trying to collect this class

    def to_json(self) -> dict[str, Any]:
        return {
            "nodeid": self.nodeid,
            "outcome": self.outcome.value,
            "duration": round(self.duration, 6),
            "message": self.message,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TestResult:
        return cls(
            nodeid=str(data["nodeid"]),
            outcome=Outcome(data["outcome"]),
            duration=float(data["duration"]),
            message=str(data.get("message", "")),
        )


@dataclass
class RunResult:
    spec: RunSpec
    results: list[TestResult]
    exit_code: int
    duration: float
    crashed: bool = False  # worker died / timed out before reporting
    error: str = ""  # redacted reason when crashed
    # nodeid -> state it changed (only for tests listed in spec.snapshot)
    state_changes: dict[str, list[StateChange]] = field(default_factory=dict)
    # nodeid -> main-thread sleep call sites [{"site", "seconds", "count"}]
    sleep_sites: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def outcome_of(self, nodeid: str) -> Outcome | None:
        for r in self.results:
            if r.nodeid == nodeid:
                return r.outcome
        return None
