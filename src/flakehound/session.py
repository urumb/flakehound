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
from flakehound.detectors.probes import Probe, make_probes
from flakehound.detectors.shake import TestStats, Verdict, analyze, plan_rechecks, plan_runs
from flakehound.diagnosis.common import failed
from flakehound.diagnosis.env import EnvCause, EnvDiagnosis, confirm_probe, diagnose_seeds
from flakehound.diagnosis.order import OrderDiagnosis, diagnose_order
from flakehound.diagnosis.timing import diagnose_timing

REPORT_SCHEMA_VERSION = 3


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
    probe_results: list[RunResult] = field(default_factory=list)  # same order as probes
    diagnoses: dict[str, OrderDiagnosis] = field(default_factory=dict)
    env_diagnoses: dict[str, EnvDiagnosis] = field(default_factory=dict)
    latent: list[EnvDiagnosis] = field(default_factory=list)  # time bombs and racy tests
    probes: list[Probe] = field(default_factory=list)
    diagnosis_runs: int = 0
    not_diagnosed: int = 0  # flaky tests skipped because of --max-diagnose

    def by_verdict(self, verdict: Verdict) -> list[TestStats]:
        return [s for s in self.stats if s.verdict is verdict]

    @property
    def crashed_runs(self) -> list[RunResult]:
        return [r for r in self.run_results + self.probe_results if r.crashed]

    def _probe_outcome(self, i: int) -> dict[str, Any]:
        if i >= len(self.probe_results):
            return {"ran": False}
        r = self.probe_results[i]
        return {
            "ran": True,
            "crashed": r.crashed,
            "error": r.error,
            "failed_tests": [t.nodeid for t in r.results if t.outcome.is_failure],
        }

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
            "probes": [
                {
                    "name": p.name,
                    "label": p.label,
                    "env": p.env,
                    "clock": p.clock,
                    "sleep_scale": p.sleep_scale,
                    "switch_interval": p.switch_interval,
                    **(self._probe_outcome(i)),
                }
                for i, p in enumerate(self.probes)
            ],
            "latent": [{"nodeid": d.nodeid, **d.to_json()} for d in self.latent],
            "tests_detail": [
                {
                    **s.to_json(),
                    "diagnosis": (
                        self.diagnoses[s.nodeid].to_json() if s.nodeid in self.diagnoses else None
                    ),
                    "env_diagnosis": (
                        self.env_diagnoses[s.nodeid].to_json()
                        if s.nodeid in self.env_diagnoses
                        else None
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


def _probe_specs(base: RunSpec, probes: list[Probe], first_id: int) -> list[RunSpec]:
    """Same order and seeds as the baseline run; only the timezone or clock differs."""
    return [
        RunSpec(
            run_id=first_id + i,
            order=list(base.order),
            seed=base.seed,
            hash_seed=base.hash_seed,
            env={**base.env, **p.env},
            clock=p.clock,
            sleep_scale=p.sleep_scale,
            switch_interval=p.switch_interval,
            record_sleeps=p.sleep_scale is not None,
            label=f"probe {p.name}",
        )
        for i, p in enumerate(probes)
    ]


def _bomb_candidates(
    baseline: RunResult, probe_results: list[RunResult], probes: list[Probe], skip: set[str]
) -> dict[str, list[Probe]]:
    """Tests that passed in the baseline but failed under a probe."""
    found: dict[str, list[Probe]] = {}
    passed = {
        r.nodeid
        for r in baseline.results
        if not r.outcome.is_failure and r.outcome.value == "passed"
    }
    for probe, result in zip(probes, probe_results, strict=True):
        for r in result.results:
            if r.outcome.is_failure and r.nodeid in passed and r.nodeid not in skip:
                found.setdefault(r.nodeid, []).append(probe)
    return found


def run_session(
    runner: Runner,
    procs: ProcessRegistry,
    *,
    runs: int,
    workers: int,
    master_seed: int,
    diagnose: bool = True,
    max_diagnose: int = 20,
    probe: bool = True,
    timing_probes: bool = True,
    on_collected: Callable[[int, int], None] | None = None,
    on_result: Callable[[RunResult], None] | None = None,
    on_diagnose_start: Callable[[int], None] | None = None,
    on_diagnosed: Callable[[str], None] | None = None,
) -> Report:
    """``on_collected(n_tests, n_runs)``: called once the tests are known."""
    start = time.perf_counter()
    diagnoses: dict[str, OrderDiagnosis] = {}
    env_diagnoses: dict[str, EnvDiagnosis] = {}
    latent: list[EnvDiagnosis] = []
    diagnosis_runs = 0
    probes = make_probes(timing=timing_probes) if probe else []
    with private_workdir() as workdir:
        test_ids = runner.collect(workdir)
        results: list[RunResult] = []
        probe_results: list[RunResult] = []
        if test_ids:
            specs = plan_runs(test_ids, runs, master_seed)
            specs += _probe_specs(specs[0], probes, first_id=len(specs))
            if on_collected is not None:
                on_collected(len(test_ids), len(specs))
            all_results = run_all(
                runner, specs, workdir, procs, workers=workers, on_result=on_result
            )
            results, probe_results = all_results[:runs], all_results[runs:]
        elif on_collected is not None:
            on_collected(0, 0)
        stats = analyze(test_ids, results)
        broken_now = [s.nodeid for s in stats if s.verdict is Verdict.BROKEN][:max_diagnose]
        if broken_now and diagnose:
            rechecks = plan_rechecks(
                broken_now, master_seed, first_id=len(results) + len(probe_results)
            )
            results = results + run_all(runner, rechecks, workdir, procs, workers=workers)
            stats = analyze(test_ids, results)
        flaky = [s.nodeid for s in stats if s.verdict is Verdict.FLAKY]
        victims = flaky[:max_diagnose] if diagnose else []
        broken = {s.nodeid for s in stats if s.verdict is Verdict.BROKEN}
        bombs: dict[str, list[Probe]] = {}
        probe_sites: dict[str, list[dict[str, Any]]] = {}
        if diagnose and probe_results:
            bombs = _bomb_candidates(results[0], probe_results, probes, broken)
            for pr in probe_results:
                for nodeid, sites in pr.sleep_sites.items():
                    probe_sites.setdefault(nodeid, []).extend(sites)
        if victims or bombs:
            if on_diagnose_start is not None:
                on_diagnose_start(len(victims) + len(bombs))
            executor = Executor(
                runner, workdir, procs, workers=workers, first_run_id=len(results) + len(probes)
            )
            baseline = results[0]

            def flaky_job(victim: str) -> None:
                order = diagnose_order(victim, results, executor, runner)
                diagnoses[victim] = order
                # A truly random test can look order-dependent by chance in a few
                # isolation runs; unless order is *confirmed*, check seeds too.
                if not (order.is_order and order.confirmed):
                    env = diagnose_seeds(victim, results, executor, runner)
                    env_diagnoses[victim] = env
                    if env.cause is EnvCause.NONDETERMINISTIC:
                        # Same seeds as a run where it failed; only timing changes.
                        failing = next(r.spec for r in results if failed(r, victim) is True)
                        timing = diagnose_timing(victim, failing, executor, runner, latent=False)
                        if timing is not None:
                            timing.evidence.insert(0, env.evidence[0])
                            env_diagnoses[victim] = timing

            def bomb_job(victim: str) -> None:
                found = bombs[victim]
                env_probes = [p for p in found if p.kind != "timing"]
                if env_probes:
                    latent.append(
                        confirm_probe(victim, env_probes, baseline.spec, executor, runner)
                    )
                names = {p.name for p in found if p.kind == "timing"}
                if names:
                    sites = probe_sites.get(victim, [])
                    diag = diagnose_timing(
                        victim,
                        baseline.spec,
                        executor,
                        runner,
                        latent=True,
                        known_sites=sites,
                        try_sleeps="short_waits" in names,
                        try_threads="thread_stress" in names,
                    )
                    if diag is not None:
                        latent.append(diag)

            try:
                # Tests are diagnosed concurrently; the executor bounds the
                # number of test processes running at once to `workers`.
                jobs = [(v, flaky_job) for v in victims] + [(v, bomb_job) for v in bombs]
                with ThreadPoolExecutor(max_workers=min(len(jobs), workers)) as pool:
                    futures = [(v, pool.submit(job, v)) for v, job in jobs]
                    for victim, fut in futures:
                        fut.result()
                        if on_diagnosed is not None:
                            on_diagnosed(victim)
            except BaseException:
                executor.abort()
                raise
            executor.close()
            diagnosis_runs = executor.runs_used
    latent.sort(key=lambda d: (d.nodeid, d.cause.value))
    return Report(
        runner=runner.name,
        master_seed=master_seed,
        runs=runs,
        workers=workers,
        test_ids=test_ids,
        duration=time.perf_counter() - start,
        stats=stats,
        run_results=results,
        probe_results=probe_results,
        diagnoses=diagnoses,
        env_diagnoses=env_diagnoses,
        latent=latent,
        probes=probes,
        diagnosis_runs=diagnosis_runs,
        not_diagnosed=len(flaky) - len(victims) if diagnose else 0,
    )
