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
class RunSpec:
    """One experiment: run these tests, in this order, under these conditions."""

    run_id: int
    order: list[str]
    seed: int
    hash_seed: int | None = None
    env: dict[str, str] = field(default_factory=dict)
    label: str = ""

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

    def outcome_of(self, nodeid: str) -> Outcome | None:
        for r in self.results:
            if r.nodeid == nodeid:
                return r.outcome
        return None
