"""Orchestrates one ``flakehound run``: collect, shake, analyze, diagnose."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from flakehound import __version__
from flakehound.adapters.base import Runner
from flakehound.core.executor import Executor
from flakehound.core.models import RunResult, RunSpec
from flakehound.core.procs import ProcessRegistry
from flakehound.core.scheduler import private_workdir, run_all
from flakehound.detectors.shake import TestStats, Verdict, analyze, plan_runs
from flakehound.diagnosis.order import OrderDiagnosis, diagnose_order

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
    diagnoses: dict[str, OrderDiagnosis] = field(default_factory=dict)
    diagnosis_runs: int = 0
    not_diagnosed: int = 0  # flaky tests skipped because of --max-diagnose

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
            "diagnosis_runs": self.diagnosis_runs,
            "tests_detail": [
                {
                    **s.to_json(),
                    "diagnosis": (
                        self.diagnoses[s.nodeid].to_json() if s.nodeid in self.diagnoses else None
                    ),
                }
                for s in self.stats
            ],
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
    diagnose: bool = True,
    max_diagnose: int = 20,
    on_collected: Callable[[int], None] | None = None,
    on_result: Callable[[RunResult], None] | None = None,
    on_diagnose_start: Callable[[int], None] | None = None,
    on_diagnosed: Callable[[OrderDiagnosis], None] | None = None,
) -> Report:
    start = time.perf_counter()
    diagnoses: dict[str, OrderDiagnosis] = {}
    diagnosis_runs = 0
    with private_workdir() as workdir:
        test_ids = runner.collect(workdir)
        if on_collected is not None:
            on_collected(len(test_ids))
        results: list[RunResult] = []
        if test_ids:
            specs = plan_runs(test_ids, runs, master_seed)
            results = run_all(runner, specs, workdir, procs, workers=workers, on_result=on_result)
        stats = analyze(test_ids, results)
        flaky = [s.nodeid for s in stats if s.verdict is Verdict.FLAKY]
        victims = flaky[:max_diagnose] if diagnose else []
        if victims:
            if on_diagnose_start is not None:
                on_diagnose_start(len(victims))
            executor = Executor(runner, workdir, procs, workers=workers, first_run_id=len(results))
            try:
                # Victims are diagnosed concurrently; the executor bounds the
                # number of test processes running at once to `workers`.
                with ThreadPoolExecutor(max_workers=min(len(victims), workers)) as pool:
                    futures = {
                        v: pool.submit(diagnose_order, v, results, executor, runner)
                        for v in victims
                    }
                    for victim, fut in futures.items():
                        diagnoses[victim] = fut.result()
                        if on_diagnosed is not None:
                            on_diagnosed(diagnoses[victim])
            except BaseException:
                executor.abort()
                raise
            executor.close()
            diagnosis_runs = executor.runs_used
    return Report(
        runner=runner.name,
        master_seed=master_seed,
        runs=runs,
        workers=workers,
        test_ids=test_ids,
        duration=time.perf_counter() - start,
        stats=stats,
        run_results=results,
        diagnoses=diagnoses,
        diagnosis_runs=diagnosis_runs,
        not_diagnosed=len(flaky) - len(victims) if diagnose else 0,
    )
