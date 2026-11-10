"""Helpers shared by the diagnosers."""

from __future__ import annotations

from typing import Any

from flakehound.adapters.base import Runner
from flakehound.core.executor import Request
from flakehound.core.models import Outcome, Repro, RunResult, RunSpec


def failed(result: RunResult, nodeid: str) -> bool | None:
    """True/False if ``nodeid`` failed/passed in ``result``; None if it didn't run."""
    if result.crashed:
        return None
    outcome = result.outcome_of(nodeid)
    if outcome is None or outcome is Outcome.SKIPPED:
        return None
    return outcome.is_failure


def request(spec: RunSpec, order: list[str], **overrides: Any) -> Request:
    """Same conditions as ``spec`` unless overridden: change one factor at a time."""
    fields: dict[str, Any] = {
        "order": order,
        "seed": spec.seed,
        "hash_seed": spec.hash_seed,
        "env": dict(spec.env),
        "clock": spec.clock,
    }
    fields.update(overrides)
    return Request(**fields)


def verify_repro(runner: Runner, repro: Repro) -> bool:
    """Run the exact repro command we will print; never let a check crash a report."""
    try:
        return runner.check_repro(repro)
    except OSError:
        return False
