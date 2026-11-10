"""Detection phase: "shake everything".

Run the whole suite several times, each time with a different test order,
random seed and hash seed. Every run's conditions are recorded, so any failure
can be replayed exactly. Tests that both pass and fail are flaky suspects;
later phases work out *why*.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from flakehound.core.models import Outcome, RunResult, RunSpec
from flakehound.core.stats import wilson_interval

_MAX_HASH_SEED = 4_294_967_295


def plan_runs(test_ids: list[str], runs: int, master_seed: int) -> list[RunSpec]:
    """Run 0 keeps the default order (a baseline); the rest are shuffled."""
    rng = random.Random(master_seed)  # noqa: S311 - reproducible test orders, not crypto
    specs: list[RunSpec] = []
    for run_id in range(runs):
        order = list(test_ids)
        label = "default order"
        if run_id > 0:
            rng.shuffle(order)
            label = "shuffled"
        specs.append(
            RunSpec(
                run_id=run_id,
                order=order,
                seed=rng.randrange(2**32),
                hash_seed=rng.randrange(_MAX_HASH_SEED + 1),
                label=label,
            )
        )
    return specs


RECHECK_RUNS = 4


def plan_rechecks(
    broken: list[str], master_seed: int, first_id: int, per_test: int = RECHECK_RUNS
) -> list[RunSpec]:
    """Extra runs for tests that failed every time: alone, with fresh seeds.

    A test that passes only 1 time in 6 can fail all of a small number of runs
    by chance. Rechecking catches some of those before we call them broken.
    """
    rng = random.Random(f"recheck:{master_seed}")  # noqa: S311 - reproducible, not crypto
    specs: list[RunSpec] = []
    for nodeid in broken:
        for _ in range(per_test):
            specs.append(
                RunSpec(
                    run_id=first_id + len(specs),
                    order=[nodeid],
                    seed=rng.randrange(2**32),
                    hash_seed=rng.randrange(_MAX_HASH_SEED + 1),
                    label="recheck alone",
                )
            )
    return specs


class Verdict(str, Enum):
    FLAKY = "flaky"  # passed in some runs, failed in others
    BROKEN = "broken"  # failed in every run: a real failure, not flakiness
    STABLE = "stable"  # passed in every run
    SKIPPED = "skipped"
    NOT_RUN = "not_run"  # every run that should have run it crashed


@dataclass
class TestStats:
    nodeid: str
    runs: int = 0
    failures: int = 0
    skips: int = 0
    failing_runs: list[int] = field(default_factory=list)
    messages: Counter[str] = field(default_factory=Counter)  # failure location -> count
    examples: dict[str, str] = field(default_factory=dict)  # failure location -> example

    __test__ = False

    @property
    def verdict(self) -> Verdict:
        if self.runs == 0:
            return Verdict.SKIPPED if self.skips else Verdict.NOT_RUN
        if self.failures == 0:
            return Verdict.STABLE
        if self.failures == self.runs:
            return Verdict.BROKEN
        return Verdict.FLAKY

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.failures, self.runs)

    @property
    def max_pass_rate(self) -> float:
        """Upper 95% bound on how often it could pass (meaningful for BROKEN)."""
        return wilson_interval(self.runs - self.failures, self.runs)[1]

    def top_messages(self, n: int = 3) -> list[tuple[str, int]]:
        """Most common failures as (example text, count), grouped by location."""
        return [(self.examples[key], count) for key, count in self.messages.most_common(n)]

    def to_json(self) -> dict[str, Any]:
        lo, hi = self.interval
        return {
            "nodeid": self.nodeid,
            "verdict": self.verdict.value,
            "runs": self.runs,
            "failures": self.failures,
            "skips": self.skips,
            "failure_rate": round(self.failure_rate, 4),
            "failure_rate_ci95": [round(lo, 4), round(hi, 4)],
            "failing_runs": self.failing_runs,
            "messages": [{"text": t, "count": c} for t, c in self.top_messages()],
        }


def summarize_failure(message: str) -> tuple[str, str]:
    """Return (group key, readable example) for a pytest failure message.

    pytest prints the error detail on lines starting with ``E`` and ends with a
    ``path:line: ExceptionType`` location line. Failures are grouped by that
    location line, so ``assert 0.83 < 0.8`` and ``assert 0.95 < 0.8`` count as
    one failure mode.
    """
    lines = [ln.rstrip() for ln in message.strip().splitlines() if ln.strip()]
    if not lines:
        return ("(no message)", "(no message)")
    location = lines[-1].strip().replace("\\", "/")  # Windows paths: same style as test ids
    detail = next((ln[1:].strip() for ln in lines if ln.startswith("E ")), "")
    if not detail:
        return (location, location)
    loc = location.rsplit(":", 1)[0] if location.count(":") >= 2 else location
    return (location, f"{detail}  ({loc})")


def analyze(test_ids: list[str], results: list[RunResult]) -> list[TestStats]:
    stats = {nodeid: TestStats(nodeid) for nodeid in test_ids}
    for run in results:
        for r in run.results:
            s = stats.get(r.nodeid)
            if s is None:  # test appeared at run time (e.g. dynamic params)
                s = stats[r.nodeid] = TestStats(r.nodeid)
            if r.outcome is Outcome.SKIPPED:
                s.skips += 1
                continue
            s.runs += 1
            if r.outcome.is_failure:
                s.failures += 1
                s.failing_runs.append(run.spec.run_id)
                key, example = summarize_failure(r.message)
                s.messages[key] += 1
                s.examples.setdefault(key, example)
    order = {v: i for i, v in enumerate(Verdict)}
    return sorted(stats.values(), key=lambda s: (order[s.verdict], -s.failure_rate, s.nodeid))
