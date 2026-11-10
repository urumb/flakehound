"""Orchestrates one ``flakehound run``: collect, shake, analyze."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flakehound import __version__
from flakehound.adapters.base import Runner
from flakehound.core.models import RunResult, RunSpec
from flakehound.core.procs import ProcessRegistry
from flakehound.core.scheduler import private_workdir, run_all
from flakehound.detectors.shake import TestStats, Verdict, analyze, plan_runs

REPORT_SCHEMA_VERSION = 1


@dataclass
class Report:
    runner: str
    master_seed: int
    runs: int
    workers: int
    test_ids: list[str]
    duration: float
    stats: list[TestStats]
    run_results: list[RunResult]

    def by_verdict(self, verdict: Verdict) -> list[TestStats]:
        return [s for s in self.stats if s.verdict is verdict]

    @property
    def crashed_runs(self) -> list[RunResult]:
        return [r for r in self.run_results if r.crashed]

    def spec(self, run_id: int) -> RunSpec:
        return self.run_results[run_id].spec

    def to_json(self) -> dict[str, Any]:
        failing = {rid for s in self.stats for rid in s.failing_runs}
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "tool": {"name": "flakehound", "version": __version__},
            "runner": self.runner,
            "master_seed": self.master_seed,
            "runs": self.runs,
            "workers": self.workers,
            "tests": len(self.test_ids),
            "duration_seconds": round(self.duration, 3),
            "summary": {v.value: len(self.by_verdict(v)) for v in Verdict},
            "tests_detail": [s.to_json() for s in self.stats],
            # Exact conditions of every run that had a failure, for replay.
            "failing_runs": [
                {**r.spec.to_json(), "crashed": r.crashed, "error": r.error}
                for r in self.run_results
                if r.spec.run_id in failing or r.crashed
            ],
        }


def run_session(
    runner: Runner,
    procs: ProcessRegistry,
    *,
    runs: int,
    workers: int,
    master_seed: int,
    on_collected: Callable[[int], None] | None = None,
    on_result: Callable[[RunResult], None] | None = None,
) -> Report:
    start = time.perf_counter()
    with private_workdir() as workdir:
        test_ids = runner.collect(workdir)
        if on_collected is not None:
            on_collected(len(test_ids))
        results: list[RunResult] = []
        if test_ids:
            specs = plan_runs(test_ids, runs, master_seed)
            results = run_all(runner, specs, workdir, procs, workers=workers, on_result=on_result)
    return Report(
        runner=runner.name,
        master_seed=master_seed,
        runs=runs,
        workers=workers,
        test_ids=test_ids,
        duration=time.perf_counter() - start,
        stats=analyze(test_ids, results),
        run_results=results,
    )
