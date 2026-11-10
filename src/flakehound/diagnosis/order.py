"""Order-dependency diagnosis: is the flaky test a victim of test order, and of whom?

The method changes one factor at a time:

1. **Isolate.** Run the flaky test alone, using the exact seeds of the runs where
   it failed. Only the order changed, so if it now passes, order is the cause.
   (If it fails alone using the seeds of the runs where it *passed*, it instead
   depends on another test having set something up first.)
2. **Find the culprit.** Take a failing run's prefix (the tests that ran before
   the victim). First try the tests whose position best correlates with the
   failure, in one parallel batch. If none is enough on its own, shrink the
   prefix with delta debugging.
3. **Confirm.** Re-run culprit(s) + victim several times. While doing so, snapshot
   global state around the culprits to name what they leaked.
4. **Verify the repro.** Run the printed plain-pytest command for real.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from flakehound.adapters.base import Runner
from flakehound.core.executor import Executor
from flakehound.core.models import Repro, RunResult, StateChange
from flakehound.diagnosis.common import failed as _failed
from flakehound.diagnosis.common import request as _request
from flakehound.diagnosis.common import verify_repro
from flakehound.diagnosis.ddmin import ddmin
from flakehound.diagnosis.fixes import suggest_fix

ALONE_TRIALS = 3
CONFIRM_TRIALS = 3
FAST_PATH_CANDIDATES = 4


class OrderKind(str, Enum):
    POLLUTED = "order_polluted"  # fails after a polluter test
    NEEDS_SETTER = "order_needs_setter"  # passes only after a state-setter test
    NOT_ORDER = "not_order"  # order is not the cause
    INCONCLUSIVE = "inconclusive"


@dataclass
class Evidence:
    """How the victim did in the shake runs, split by the culprits' position."""

    before_failed: int = 0
    before_total: int = 0
    otherwise_failed: int = 0
    otherwise_total: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "culprits_before_failed": self.before_failed,
            "culprits_before_total": self.before_total,
            "otherwise_failed": self.otherwise_failed,
            "otherwise_total": self.otherwise_total,
        }


@dataclass
class OrderDiagnosis:
    nodeid: str
    kind: OrderKind
    culprits: list[str] = field(default_factory=list)
    confirmed: bool = False
    confirm_reproduced: int = 0
    confirm_total: int = 0
    alone_failed: int = 0  # alone, with the seeds of runs where it failed
    alone_total: int = 0
    alone_pass_seeds_failed: int = 0  # alone, with the seeds of runs where it passed
    alone_pass_seeds_total: int = 0
    evidence: Evidence = field(default_factory=Evidence)
    state_changes: list[StateChange] = field(default_factory=list)
    repro: list[str] = field(default_factory=list)
    repro_verified: bool | None = None
    fix: str = ""
    note: str = ""
    runs_used: int = 0

    @property
    def is_order(self) -> bool:
        return self.kind in (OrderKind.POLLUTED, OrderKind.NEEDS_SETTER)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "culprits": self.culprits,
            "confirmed": self.confirmed,
            "confirm": {"reproduced": self.confirm_reproduced, "total": self.confirm_total},
            "alone_with_failing_seeds": {"failed": self.alone_failed, "total": self.alone_total},
            "alone_with_passing_seeds": {
                "failed": self.alone_pass_seeds_failed,
                "total": self.alone_pass_seeds_total,
            },
            "evidence": self.evidence.to_json(),
            "state_changes": [c.to_json() for c in self.state_changes],
            "repro": self.repro,
            "repro_verified": self.repro_verified,
            "fix": self.fix,
            "note": self.note,
            "runs_used": self.runs_used,
        }


def _rank_candidates(victim: str, runs: Sequence[RunResult], *, want_fail: bool) -> list[str]:
    """Tests whose running-before-the-victim best correlates with the bad outcome."""
    bad_before: dict[str, int] = {}
    good_before: dict[str, int] = {}
    n_bad = n_good = 0
    for run in runs:
        outcome = _failed(run, victim)
        if outcome is None or victim not in run.spec.order:
            continue
        bad = outcome if want_fail else not outcome
        n_bad += bad
        n_good += not bad
        target = bad_before if bad else good_before
        for t in run.spec.order[: run.spec.order.index(victim)]:
            target[t] = target.get(t, 0) + 1
    scores = {
        t: bad_before.get(t, 0) / max(n_bad, 1) - good_before.get(t, 0) / max(n_good, 1)
        for t in set(bad_before) | set(good_before)
    }
    ranked = sorted((t for t, s in scores.items() if s > 0), key=lambda t: (-scores[t], t))
    return ranked[:FAST_PATH_CANDIDATES]


def _evidence(victim: str, culprits: list[str], runs: Sequence[RunResult]) -> Evidence:
    ev = Evidence()
    for run in runs:
        outcome = _failed(run, victim)
        if outcome is None:
            continue
        order = run.spec.order
        pos = order.index(victim)
        before = all(c in order and order.index(c) < pos for c in culprits)
        if before:
            ev.before_total += 1
            ev.before_failed += outcome
        else:
            ev.otherwise_total += 1
            ev.otherwise_failed += outcome
    return ev


def diagnose_order(
    victim: str,
    shake_runs: Sequence[RunResult],
    executor: Executor,
    runner: Runner,
) -> OrderDiagnosis:
    start_runs = executor.runs_used
    diag = _diagnose(victim, shake_runs, executor, runner)
    diag.runs_used = executor.runs_used - start_runs
    diag.fix = suggest_fix(diag)
    return diag


def _diagnose(
    victim: str, shake_runs: Sequence[RunResult], executor: Executor, runner: Runner
) -> OrderDiagnosis:
    failing = [r for r in shake_runs if _failed(r, victim) is True]
    passing = [r for r in shake_runs if _failed(r, victim) is False]
    diag = OrderDiagnosis(victim, OrderKind.INCONCLUSIVE)
    if not failing or not passing:
        diag.note = "needs at least one passing and one failing run"
        return diag

    # 1. Isolate: alone, under the failing runs' seeds and the passing runs' seeds.
    f_specs = [r.spec for r in failing[:ALONE_TRIALS]]
    p_specs = [r.spec for r in passing[:ALONE_TRIALS]]
    alone = executor.run_many([_request(s, [victim], label="alone") for s in f_specs + p_specs])
    alone_f = [x for x in (_failed(r, victim) for r in alone[: len(f_specs)]) if x is not None]
    alone_p = [x for x in (_failed(r, victim) for r in alone[len(f_specs) :]) if x is not None]
    diag.alone_failed, diag.alone_total = sum(alone_f), len(alone_f)
    diag.alone_pass_seeds_failed, diag.alone_pass_seeds_total = sum(alone_p), len(alone_p)
    if not alone_f or not alone_p:
        diag.note = "the test could not be run on its own"
        return diag

    if not any(alone_f):
        kind, want_fail, source = OrderKind.POLLUTED, True, failing
    elif all(alone_p):
        kind, want_fail, source = OrderKind.NEEDS_SETTER, False, passing
    else:
        diag.kind = OrderKind.NOT_ORDER
        diag.note = (
            f"fails on its own ({diag.alone_failed}/{diag.alone_total} runs with the seeds "
            "of the failing runs), so test order is not the cause"
        )
        return diag

    # 2. Find the culprit(s) in the shortest suitable prefix.
    run = min(source, key=lambda r: r.spec.order.index(victim))
    prefix = run.spec.order[: run.spec.order.index(victim)]
    if not prefix:
        diag.note = "no tests ran before it in the run to investigate"
        return diag
    cache: dict[tuple[str, ...], bool] = {}

    def reproduces_many(subsets: list[list[str]]) -> list[bool]:
        todo = [s for s in subsets if tuple(s) not in cache]
        results = executor.run_many(
            [_request(run.spec, [*s, victim], label="bisect") for s in todo]
        )
        for subset, result in zip(todo, results, strict=True):
            outcome = _failed(result, victim)
            cache[tuple(subset)] = outcome is not None and outcome == want_fail
        return [cache[tuple(s)] for s in subsets]

    candidates = [
        c for c in _rank_candidates(victim, shake_runs, want_fail=want_fail) if c in prefix
    ]
    batch = [prefix] + [[c] for c in candidates]
    answers = reproduces_many(batch)
    singles = [c for c, ok in zip(candidates, answers[1:], strict=True) if ok]
    if singles:
        culprits = singles  # each one alone is enough
    elif answers[0]:
        culprits = ddmin(prefix, reproduces_many)
    else:
        diag.note = "could not reproduce the failing order again (it may also be timing-related)"
        return diag

    # 3. Confirm, with state snapshots around the culprits on the first trial.
    order = [*_in_order(culprits, prefix), victim]
    confirm = executor.run_many(
        [
            _request(run.spec, order, snapshot=culprits if i == 0 else [], label="confirm")
            for i in range(CONFIRM_TRIALS)
        ]
    )
    outcomes = [_failed(r, victim) for r in confirm]
    diag.kind = kind
    diag.culprits = _in_order(culprits, prefix)
    diag.confirm_total = sum(o is not None for o in outcomes)
    diag.confirm_reproduced = sum(o is not None and o == want_fail for o in outcomes)
    diag.confirmed = diag.confirm_total > 0 and diag.confirm_reproduced == diag.confirm_total
    for c in diag.culprits:
        diag.state_changes.extend(confirm[0].state_changes.get(c, []))
    diag.evidence = _evidence(victim, diag.culprits, shake_runs)

    # 4. A plain pytest command that shows the problem, verified for real.
    repro = Repro(order if kind is OrderKind.POLLUTED else [victim])
    diag.repro = runner.repro_command(repro)
    diag.repro_verified = verify_repro(runner, repro)
    return diag


def _in_order(tests: list[str], reference: list[str]) -> list[str]:
    pos = {t: i for i, t in enumerate(reference)}
    return sorted(tests, key=lambda t: pos.get(t, len(pos)))
