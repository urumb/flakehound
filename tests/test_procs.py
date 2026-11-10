import os
import sys
import time
from pathlib import Path

import pytest

from flakehound.core.procs import ProcessRegistry, StoppedError


def test_rejects_string_commands(tmp_path: Path):
    with pytest.raises(TypeError):
        ProcessRegistry().run("echo hi", cwd=tmp_path, env={}, timeout=5)  # type: ignore[arg-type]


def test_runs_and_captures(tmp_path: Path):
    out = ProcessRegistry().run(
        [sys.executable, "-c", "print('hi')"], cwd=tmp_path, env=dict(os.environ), timeout=30
    )
    assert out.returncode == 0
    assert out.stdout.strip() == "hi"
    assert not out.timed_out


def test_timeout_kills_process(tmp_path: Path):
    start = time.monotonic()
    out = ProcessRegistry().run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout=1,
    )
    assert out.timed_out
    assert time.monotonic() - start < 30


def test_no_spawning_after_kill_all(tmp_path: Path):
    reg = ProcessRegistry()
    reg.kill_all()
    with pytest.raises(StoppedError):
        reg.run([sys.executable, "-c", "pass"], cwd=tmp_path, env=dict(os.environ), timeout=5)
