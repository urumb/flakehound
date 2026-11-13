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


LAZY = """
import functools

class LazySettings:
    # Like Django/DRF settings: values are copied into the instance on first access.
    _defaults = {"ROUTERS": [], "PAGE_SIZE": 10}

    def __getattr__(self, name):
        if name not in self._defaults:
            raise AttributeError(name)
        value = self._defaults[name]
        setattr(self, name, value)
        return value

class Router:
    def __init__(self):
        self._routers = None

    @functools.cached_property
    def routers(self):
        if self._routers is None:
            self._routers = []
        return self._routers

class Holder:
    def __init__(self):
        self.cutoff = 1000
        self.text = "More than {count} items"

settings = LazySettings()
router = Router()
api_settings = Holder()
alias = settings  # the same object under a second name
"""


@pytest.fixture
def lazy_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "lazyproj"
    root.mkdir()
    (root / "fh_lazymod.py").write_text(LAZY, encoding="utf-8")
    monkeypatch.syspath_prepend(str(root))
    module = importlib.import_module("fh_lazymod")
    yield root, module
    sys.modules.pop("fh_lazymod", None)


def test_lazy_caches_are_not_leaks(lazy_project):
    root, mod = lazy_project

    def touch():
        _ = mod.settings.ROUTERS, mod.settings.PAGE_SIZE  # cached on first access
        _ = mod.router.routers  # cached_property fills, backing field None -> []

    assert _changes(root, touch) == {}


def test_real_leak_on_object_is_still_reported(lazy_project):
    root, mod = lazy_project

    _ = mod.settings.PAGE_SIZE  # already cached before the test runs

    def leak():
        mod.settings.PAGE_SIZE = 99

    changes = _changes(root, leak)
    assert list(changes) == ["fh_lazymod.settings.PAGE_SIZE"]  # once, not also as alias
    assert changes["fh_lazymod.settings.PAGE_SIZE"].after == "99"
    assert mod.settings.PAGE_SIZE == 99  # taking snapshots changed nothing


def test_snapshot_never_calls_getattr(lazy_project):
    root, mod = lazy_project
    snapshot.take(root)
    assert "ROUTERS" not in vars(mod.settings)  # no lookup was triggered


def test_replaced_object_keeps_value_changes_drops_disappearances(lazy_project):
    root, mod = lazy_project

    class Mock:
        def __init__(self):
            self.cutoff = 2

    def swap():
        mod.api_settings = Mock()

    changes = _changes(root, swap)
    assert "fh_lazymod.api_settings" in changes  # the swap itself
    assert changes["fh_lazymod.api_settings.cutoff"].after == "2"  # real value change
    assert "fh_lazymod.api_settings.text" not in changes  # old attribute "disappearing"


def test_counters_and_testcase_bookkeeping_are_not_leaks(tmp_path, monkeypatch):
    root = tmp_path / "countproj"
    root.mkdir()
    (root / "fh_countmod.py").write_text(
        "import unittest\n"
        "class Field:\n"
        "    _creation_counter = 0\n"
        "    def __init__(self):\n"
        "        Field._creation_counter += 1\n"
        "class MyTests(unittest.TestCase):\n"
        "    pass\n"
        "count = 0\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(root))
    mod = importlib.import_module("fh_countmod")
    try:

        def run():
            mod.Field()
            mod.MyTests.client = object()  # like Django's setUpClass
            mod.count += 1  # public: a real leak

        assert list(_changes(root, run)) == ["fh_countmod.count"]
    finally:
        sys.modules.pop("fh_countmod", None)
