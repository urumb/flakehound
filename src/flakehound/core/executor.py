"""Runs ad-hoc experiments in parallel during diagnosis.

Diagnosis code asks questions like "does the victim fail after these 5 tests?".
Each question is one run. The executor runs many questions at once (bounded by
``workers``), gives every run a unique id and counts how many runs were used.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from flakehound.adapters.base import Runner
from flakehound.core.models import RunResult, RunSpec
from flakehound.core.procs import ProcessRegistry


@dataclass(frozen=True)
class Request:
    order: list[str]
    seed: int
    hash_seed: int | None
    snapshot: list[str] = field(default_factory=list)
    label: str = ""
    env: dict[str, str] = field(default_factory=dict)
    clock: str | None = None
    sleep_scale: float | None = None
    sleep_sites: list[str] = field(default_factory=list)
    switch_interval: float | None = None
    record_sleeps: bool = False


class Executor:
    def __init__(
        self,
        runner: Runner,
        workdir: Path,
        procs: ProcessRegistry,
        *,
        workers: int,
        first_run_id: int = 0,
    ) -> None:
        self.runner = runner
        self.workdir = workdir
        self.procs = procs
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="fh-diag")
        self._ids = itertools.count(first_run_id)
        self._lock = threading.Lock()
        self.runs_used = 0

    def _spec(self, req: Request) -> RunSpec:
        with self._lock:
            run_id = next(self._ids)
            self.runs_used += 1
        return RunSpec(
            run_id=run_id,
            order=list(req.order),
            seed=req.seed,
            hash_seed=req.hash_seed,
            snapshot=list(req.snapshot),
            label=req.label,
            env=dict(req.env),
            clock=req.clock,
            sleep_scale=req.sleep_scale,
            sleep_sites=list(req.sleep_sites),
            switch_interval=req.switch_interval,
            record_sleeps=req.record_sleeps,
        )

    def run_many(self, requests: Sequence[Request]) -> list[RunResult]:
        specs = [self._spec(r) for r in requests]
        futures = [self._pool.submit(self.runner.run, s, self.workdir) for s in specs]
        return [f.result() for f in futures]

    def run(self, request: Request) -> RunResult:
        return self.run_many([request])[0]

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def abort(self) -> None:
        self.procs.kill_all()
        self._pool.shutdown(wait=False, cancel_futures=True)
