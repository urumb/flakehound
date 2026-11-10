"""The adapter interface every test runner implements.

The core engine only talks to this interface, so supporting Jest, Go test or
JUnit later means writing a new adapter, not changing the engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from flakehound.core.models import RunResult, RunSpec


class CollectionError(RuntimeError):
    """The runner could not list the tests (syntax error, bad path, ...)."""


class Runner(Protocol):
    name: str

    def collect(self, workdir: Path) -> list[str]:
        """Return the ids of all tests, in the runner's default order."""
        ...

    def run(self, spec: RunSpec, workdir: Path) -> RunResult:
        """Run ``spec.order`` exactly in that order, under ``spec``'s conditions.

        ``workdir`` is a private temp directory the adapter may use for
        exchange files. Must never raise for test failures; a crashed or timed
        out run is reported with ``crashed=True``.
        """
        ...
