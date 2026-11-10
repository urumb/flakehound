"""Runs many experiments in parallel and guarantees cleanup."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path

from flakehound.adapters.base import Runner
from flakehound.core.models import RunResult, RunSpec
from flakehound.core.procs import ProcessRegistry


@contextmanager
def private_workdir() -> Iterator[Path]:
    """A temp dir only we use, removed afterwards even on errors or Ctrl+C."""
    path = Path(tempfile.mkdtemp(prefix="flakehound-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def run_all(
    runner: Runner,
    specs: Sequence[RunSpec],
    workdir: Path,
    procs: ProcessRegistry,
    *,
    workers: int,
    on_result: Callable[[RunResult], None] | None = None,
) -> list[RunResult]:
    """Run ``specs`` with at most ``workers`` in parallel, results in spec order.

    Threads are fine here: each thread just waits on a subprocess, so the GIL
    is not a bottleneck. On Ctrl+C every child process tree is killed.
    """
    results: dict[int, RunResult] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="flakehound")
    try:
        pending: set[Future[RunResult]] = {pool.submit(runner.run, s, workdir) for s in specs}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                result = fut.result()
                results[result.spec.run_id] = result
                if on_result is not None:
                    on_result(result)
    except BaseException:
        procs.kill_all()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return [results[s.run_id] for s in specs]
