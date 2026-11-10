"""Runs pytest in subprocesses, one process per experiment.

Why a fresh process per run: every run starts from a clean interpreter, so state
can only leak *within* a run (which is what we want to observe), a crashing or
hanging test can't take flakehound down, and cleanup is reliable.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from xml.etree import ElementTree

from flakehound.adapters.base import CollectionError
from flakehound.core.models import Repro, RunResult, RunSpec, StateChange, TestResult
from flakehound.core.procs import ProcessRegistry
from flakehound.report.redact import redact, truncate

PLUGIN = "flakehound.adapters.pytest.plugin"

# Plugins that reorder tests themselves would fight our ordering.
_BLOCKED_PLUGINS = ("randomly", "random_order", "cacheprovider")


class PytestRunner:
    name = "pytest"

    def __init__(
        self,
        project_dir: Path,
        pytest_args: Sequence[str] = (),
        *,
        python: str | None = None,
        procs: ProcessRegistry,
        timeout: float | None = 600.0,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.pytest_args = list(pytest_args)
        self.python = python or sys.executable
        self.procs = procs
        self.timeout = timeout
        self.base_env = dict(os.environ if base_env is None else base_env)

    def _command(self, *extra: str) -> list[str]:
        cmd = [self.python, "-m", "pytest", "-p", PLUGIN]
        for plugin in _BLOCKED_PLUGINS:
            cmd += ["-p", f"no:{plugin}"]
        cmd += ["-q", "--no-header", "-o", "console_output_style=classic"]
        cmd += list(extra)
        cmd += self.pytest_args
        return cmd

    def _env(self, spec: RunSpec | None = None) -> dict[str, str]:
        env = dict(self.base_env)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if spec is not None:
            if spec.hash_seed is not None:
                env["PYTHONHASHSEED"] = str(spec.hash_seed)
            env.update(spec.env)
        return env

    def collect(self, workdir: Path) -> list[str]:
        out = workdir / "collected.json"
        res = self.procs.run(
            self._command("--collect-only", f"--flakehound-collect-out={out}"),
            cwd=self.project_dir,
            env=self._env(),
            timeout=self.timeout,
        )
        if res.timed_out:
            raise CollectionError("pytest collection timed out")
        # Exit code 5 = no tests collected, which is not an error for us.
        if res.returncode not in (0, 5) or not out.exists():
            detail = truncate(redact((res.stdout + "\n" + res.stderr).strip()), 4000)
            raise CollectionError(f"pytest could not collect tests:\n{detail}")
        ids = json.loads(out.read_text(encoding="utf-8"))
        return [str(x) for x in ids]

    def run(self, spec: RunSpec, workdir: Path) -> RunResult:
        spec_path = workdir / f"spec-{spec.run_id}.json"
        out_path = workdir / f"result-{spec.run_id}.json"
        spec_path.write_text(json.dumps(spec.to_json()), encoding="utf-8")
        start = time.perf_counter()
        res = self.procs.run(
            self._command(f"--flakehound-spec={spec_path}", f"--flakehound-out={out_path}"),
            cwd=self.project_dir,
            env=self._env(spec),
            timeout=self.timeout,
        )
        duration = time.perf_counter() - start
        results: list[TestResult] = []
        state_changes: dict[str, list[StateChange]] = {}
        if out_path.exists():
            data = json.loads(out_path.read_text(encoding="utf-8"))
            results = [TestResult.from_json(r) for r in data["results"]]
            state_changes = {
                str(nodeid): [StateChange.from_json(c) for c in changes]
                for nodeid, changes in data.get("state_changes", {}).items()
            }
        crashed = res.timed_out or not out_path.exists()
        error = ""
        if res.timed_out:
            error = f"run timed out after {self.timeout:.0f}s"
        elif crashed:
            tail = "\n".join((res.stdout + "\n" + res.stderr).strip().splitlines()[-20:])
            error = truncate(redact(f"worker exited with code {res.returncode}:\n{tail}"))
        return RunResult(spec, results, res.returncode, duration, crashed, error, state_changes)

    def repro_command(self, repro: Repro) -> list[str]:
        """A pytest command that reproduces a finding (env vars are listed separately).

        pytest runs explicitly listed test ids in the order they are given.
        Seed and clock replay need flakehound installed (they use its plugin).
        """
        cmd = ["pytest", "-p", "no:randomly", "-p", "no:cacheprovider"]
        if repro.seed is not None or repro.clock is not None:
            cmd += ["-p", PLUGIN]
        if repro.seed is not None:
            cmd.append(f"--flakehound-seed={repro.seed}")
        if repro.clock is not None:
            cmd.append(f"--flakehound-clock={repro.clock}")
        return [*cmd, *repro.order]

    def check_repro(self, repro: Repro) -> bool:
        """Run :meth:`repro_command` for real; True if the last test failed.

        This proves the exact command we print reproduces the failure. The last
        test's outcome is read from a JUnit XML report (part of pytest itself).
        """
        cmd = [self.python, "-m", *self.repro_command(repro)]
        with tempfile.TemporaryDirectory(prefix="flakehound-repro-") as tmp:
            xml = Path(tmp) / "repro.xml"
            run_env = dict(self.base_env)
            run_env["PYTHONDONTWRITEBYTECODE"] = "1"
            run_env.update(repro.env)
            res = self.procs.run(
                [*cmd, "-q", f"--junitxml={xml}"],
                cwd=self.project_dir,
                env=run_env,
                timeout=self.timeout,
            )
            if res.timed_out or not xml.exists():
                return False
            return _last_test_failed(xml, repro.order[-1])


def _last_test_failed(xml_path: Path, nodeid: str) -> bool:
    """Did ``nodeid`` fail in a pytest JUnit XML report?"""
    # The XML is written by pytest from the user's own project, parsed with the
    # stdlib parser, which does not resolve external entities.
    tree = ElementTree.parse(xml_path)  # noqa: S314
    parts = nodeid.split("::")
    name = parts[-1]
    # JUnit classname is the dotted file path plus any test classes,
    # e.g. "tests/test_a.py::TestX::test_y" -> "tests.test_a.TestX".
    module = parts[0].removesuffix(".py").replace("/", ".").replace("\\", ".")
    classname = ".".join([module, *parts[1:-1]])
    for case in tree.iter("testcase"):
        if (
            case.get("name") == name
            and case.get("classname") == classname
            and (case.find("failure") is not None or case.find("error") is not None)
        ):
            return True
    return False
