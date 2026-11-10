"""In-process tests of state snapshots (no subprocesses)."""

import importlib
import os
import sys
from pathlib import Path

import pytest

from flakehound.adapters.pytest import snapshot
from flakehound.report.redact import REDACTED


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fh_snapmod.py").write_text(
        "items = []\n"
        "class Config:\n"
        "    debug = False\n"
        "class State:\n"
        "    def __init__(self):\n"
        "        self.currency = 'USD'\n"
        "state = State()\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(root))
    module = importlib.import_module("fh_snapmod")
    yield root, module
    sys.modules.pop("fh_snapmod", None)


def _changes(root: Path, mutate) -> dict[str, snapshot.StateChange]:
    before = snapshot.take(root)
    mutate()
    after = snapshot.take(root)
    return {c.name: c for c in snapshot.diff(before, after)}


def test_module_global_mutation(project):
    root, mod = project
    changes = _changes(root, lambda: mod.items.append("apple"))
    c = changes["fh_snapmod.items"]
    assert (c.kind, c.before, c.after) == ("global", "[]", "['apple']")


def test_class_attribute_and_object_attribute(project):
    root, mod = project

    def mutate():
        mod.Config.debug = True
        mod.state.currency = "EUR"

    changes = _changes(root, mutate)
    assert changes["fh_snapmod.Config.debug"].after == "True"
    assert changes["fh_snapmod.state.currency"].after == "'EUR'"


def test_env_secret_values_are_never_shown(project, monkeypatch):
    root, _ = project
    changes = _changes(root, lambda: monkeypatch.setenv("FH_TEST_API_TOKEN", "hunter2-secret"))
    c = changes["FH_TEST_API_TOKEN"]
    assert c.kind == "env"
    assert c.before == snapshot.MISSING
    assert c.after == REDACTED


def test_plain_env_values_are_shown(project, monkeypatch):
    root, _ = project
    changes = _changes(root, lambda: monkeypatch.setenv("FH_TEST_CURRENCY", "EUR"))
    assert changes["FH_TEST_CURRENCY"].after == "'EUR'"


def test_cwd_and_files(project, monkeypatch, tmp_path):
    root, _ = project

    def mutate():
        (root / "leftover.txt").write_text("x", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

    changes = _changes(root, mutate)
    assert changes["leftover.txt"].after == "created"
    assert changes["current directory"].kind == "cwd"


def test_no_changes_means_empty_diff(project):
    root, _ = project
    assert _changes(root, lambda: None) == {}


def test_library_modules_are_ignored(project):
    root, _ = project
    # pytest itself lives in site-packages (or is skipped by name): never reported.
    names = {name for (kind, name) in snapshot.take(root).values if kind == "global"}
    assert not any(n.startswith(("pytest", "_pytest", "os.", "flakehound")) for n in names)


def test_safe_repr_survives_broken_repr():
    class Bad:
        def __repr__(self):
            raise RuntimeError("boom")

    assert "Bad" in snapshot.safe_repr(Bad())  # never raises, still names the type
    assert "(len 100)" in snapshot.safe_repr(list(range(100)))


def test_env_snapshot_does_not_mutate_environment(project):
    root, _ = project
    before = dict(os.environ)
    snapshot.take(root)
    assert dict(os.environ) == before
