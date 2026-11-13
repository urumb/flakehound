"""Timing detection: sleep-as-wait (with the exact call site) and race conditions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from flakehound.adapters.pytest.runner import PytestRunner
from flakehound.cli import main
from flakehound.core.procs import ProcessRegistry
from flakehound.diagnosis.env import EnvCause
from flakehound.session import run_session

WAITS = """
import threading
import time


class Job:
    def __init__(self):
        self.done = False

    def run(self):
        time.sleep(0.1)  # simulated work in a background thread: never shortened
        self.done = True


def test_waits_with_sleep():
    time.sleep(0.02)  # harmless: nothing depends on it
    job = Job()
    threading.Thread(target=job.run, daemon=True).start()
    time.sleep(0.3)  # the real problem: "should be long enough"
    assert job.done


def test_no_sleep():
    assert True
"""

RACE = """
import threading


class Wallet:
    def __init__(self):
        self.balance = 0

    def top_up(self, amount):
        current = self.balance
        new = self._add(current, amount)
        self.balance = new

    @staticmethod
    def _add(a, b):
        return a + b


def test_race():
    w = Wallet()
    start = threading.Barrier(4)

    def worker():
        start.wait()
        # Few enough updates to finish inside one normal thread switch (5 ms), so the
        # race almost never happens normally, even on slow CI; under stress it always does.
        for _ in range(300):
            w.top_up(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert w.balance == 1200


def test_single_thread():
    w = Wallet()
    w.top_up(5)
    assert w.balance == 5
"""


def _session(root: Path, **kw):
    procs = ProcessRegistry()
    return run_session(
        PytestRunner(root, procs=procs, timeout=120),
        procs,
        runs=kw.pop("runs", 3),
        workers=4,
        master_seed=kw.pop("seed", 1),
        **kw,
    )


def _latent(report):
    return {d.nodeid.split("::")[-1]: d for d in report.latent}


def _finding(report, name):
    """The timing diagnosis for ``name``: latent (passes normally) or, on a slow
    or busy machine where the bug also shows up in normal runs, the flaky test's."""
    latent = _latent(report)
    if name in latent:
        return latent[name]
    env = {k.split("::")[-1]: v for k, v in report.env_diagnoses.items()}
    return env.get(name)


def _explain(report) -> str:
    """Everything we know, printed when an assertion fails (helps on remote CI)."""
    lines = [f"{s.nodeid}: {s.verdict.value} {s.failures}/{s.runs}" for s in report.stats]
    lines += [f"env {k}: {v.to_json()}" for k, v in report.env_diagnoses.items()]
    lines += [f"latent {d.nodeid}: {d.to_json()}" for d in report.latent]
    for p, r in zip(report.probes, report.probe_results, strict=False):
        failed = [t.nodeid for t in r.results if t.outcome.is_failure]
        lines.append(f"probe {p.name}: crashed={r.crashed} failed={failed} error={r.error!r}")
    return "\n".join(lines)


def test_sleep_used_as_wait_is_pinned_to_its_line(make_project):
    root = make_project({"tests/test_waits.py": WAITS})
    report = _session(root)
    latent = _latent(report)
    assert "test_waits_with_sleep" in latent, _explain(report)
    d = latent["test_waits_with_sleep"]
    assert d.cause is EnvCause.TIMING and d.latent and d.confirmed
    assert "tests/test_waits.py:19" in d.condition  # the 0.3 s wait, not the harmless one
    assert any("tests/test_waits.py:16" in e and "failed 0/3" in e for e in d.evidence)
    assert "--flakehound-sleep-sites=tests/test_waits.py:19" in d.repro
    assert d.repro_verified is True
    assert "test_no_sleep" not in latent


def test_race_condition_found_under_thread_stress(make_project):
    root = make_project({"tests/test_race.py": RACE})
    report = _session(root)
    d = _finding(report, "test_race")
    assert d is not None, _explain(report)
    assert d.cause is EnvCause.RACE and d.confirmed, _explain(report)
    assert any(a.startswith("--flakehound-switch-interval=") for a in d.repro)
    assert "Lock" in d.fix
    assert "test_single_thread" not in _latent(report)


def test_no_timing_flag(make_project, monkeypatch, tmp_path):
    root = make_project({"tests/test_waits.py": WAITS})
    monkeypatch.chdir(root)
    out = tmp_path / "r.json"
    assert main(["run", "-n", "2", "--seed", "1", "--json", str(out)]) == 1
    data = json.loads(out.read_text())
    assert data["schema_version"] == 3
    assert [x["cause"] for x in data["latent"]] == ["timing"]

    assert main(["run", "-n", "2", "--seed", "1", "--no-timing", "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["latent"] == []
    assert not any(p["name"] == "short_waits" for p in data["probes"])


def _pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "flakehound.adapters.pytest.plugin",
            *args,
        ],
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_sleep_options_shrink_only_the_named_site(make_project):
    root = make_project({"tests/test_waits.py": WAITS})
    target = "tests/test_waits.py::test_waits_with_sleep"
    assert _pytest(root, "--flakehound-sleep-scale=0.05", target).returncode == 1
    harmless = _pytest(
        root,
        "--flakehound-sleep-scale=0.05",
        "--flakehound-sleep-sites=tests/test_waits.py:16",
        target,
    )
    assert harmless.returncode == 0


def test_sleep_sites_work_through_a_symlinked_project(make_project, tmp_path):
    # macOS temp dirs are symlinks (/var -> /private/var): paths must be resolved.
    root = make_project({"tests/test_waits.py": WAITS})
    link = tmp_path / "linked"
    try:
        link.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    target = "tests/test_waits.py::test_waits_with_sleep"
    assert _pytest(link, "--flakehound-sleep-scale=0.05", target).returncode == 1
    only_harmless = _pytest(
        link,
        "--flakehound-sleep-scale=0.05",
        "--flakehound-sleep-sites=tests/test_waits.py:16",
        target,
    )
    assert only_harmless.returncode == 0


def test_invalid_sleep_scale_is_rejected(make_project):
    root = make_project({"tests/test_waits.py": WAITS})
    out = _pytest(root, "--flakehound-sleep-scale=5")
    assert out.returncode != 0
    assert "--flakehound-sleep-scale" in out.stdout + out.stderr


def test_sleep_sites_are_deduplicated_before_truncation():
    from flakehound.diagnosis.timing import MAX_SITES, _sites_from

    # Many runs report the same long sleep; a short one must not be crowded out.
    reports = [{"site": "a.py:1", "seconds": 0.3}] * 10 + [{"site": "a.py:2", "seconds": 0.02}]
    assert _sites_from(reports) == [("a.py:1", 0.3), ("a.py:2", 0.02)]
    many = [{"site": f"b.py:{i}", "seconds": i / 100} for i in range(20)]
    assert len(_sites_from(many)) == MAX_SITES
