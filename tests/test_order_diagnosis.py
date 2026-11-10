"""End-to-end order diagnosis on generated projects (real pytest subprocesses)."""

import json
from pathlib import Path

from flakehound.adapters.pytest.runner import PytestRunner
from flakehound.cli import main
from flakehound.core.procs import ProcessRegistry
from flakehound.diagnosis.order import OrderKind
from flakehound.session import run_session


def _session(root: Path, runs: int = 12, seed: int = 1):
    procs = ProcessRegistry()
    report = run_session(
        PytestRunner(root, procs=procs, timeout=120),
        procs,
        runs=runs,
        workers=4,
        master_seed=seed,
    )
    return {nodeid.split("::")[-1]: d for nodeid, d in report.diagnoses.items()}, report


def _names(nodeids: list[str]) -> list[str]:
    return [n.split("::")[-1] for n in nodeids]


FILLER = "\n".join(f"def test_filler_{i}():\n    assert True\n" for i in range(8))


def test_polluter_found_with_leaked_global(make_project):
    root = make_project(
        {
            "app.py": "cache = {}\n",
            "tests/test_a.py": "import app\n\n"
            "def test_victim():\n    assert app.cache == {}\n\n"
            "def test_polluter():\n    app.cache['user'] = 1\n\n" + FILLER,
        }
    )
    diags, _ = _session(root)
    d = diags["test_victim"]
    assert d.kind is OrderKind.POLLUTED
    assert _names(d.culprits) == ["test_polluter"]
    assert d.confirmed
    assert d.repro_verified is True
    assert any(c.name == "app.cache" and "user" in c.after for c in d.state_changes)
    assert d.evidence.before_failed == d.evidence.before_total > 0
    assert d.evidence.otherwise_failed == 0
    assert "app.cache" in d.fix


def test_env_polluter_and_secret_redacted(make_project, tmp_path):
    root = make_project(
        {
            "tests/test_env.py": "import os\n\n"
            "def test_victim():\n    assert 'DEPLOY_TOKEN' not in os.environ\n\n"
            "def test_polluter():\n    os.environ['DEPLOY_TOKEN'] = 'tok-very-secret-123'\n\n"
            + FILLER,
        }
    )
    diags, report = _session(root, seed=4)
    d = diags["test_victim"]
    assert d.kind is OrderKind.POLLUTED
    change = next(c for c in d.state_changes if c.name == "DEPLOY_TOKEN")
    assert change.kind == "env"
    assert "tok-very-secret-123" not in change.after
    assert "monkeypatch.setenv" in d.fix
    assert "tok-very-secret-123" not in json.dumps(report.to_json())


def test_needs_setter(make_project):
    root = make_project(
        {
            "registry.py": "plugins = []\n",
            "tests/test_s.py": "import registry\n\n"
            "def test_setter():\n    registry.plugins.append('x')\n\n"
            "def test_needs_it():\n    assert registry.plugins\n\n" + FILLER,
        }
    )
    diags, _ = _session(root, seed=2)
    d = diags["test_needs_it"]
    assert d.kind is OrderKind.NEEDS_SETTER
    assert _names(d.culprits) == ["test_setter"]
    assert d.confirmed
    assert d.repro_verified is True  # running it alone fails


def test_combined_polluters_found_by_ddmin(make_project):
    # The victim only fails when BOTH a and b ran before it.
    root = make_project(
        {
            "flags.py": "a = False\nb = False\n",
            "tests/test_c.py": "import flags\n\n"
            "def test_set_a():\n    flags.a = True\n\n"
            "def test_set_b():\n    flags.b = True\n\n"
            "def test_victim():\n    assert not (flags.a and flags.b)\n\n" + FILLER,
        }
    )
    diags, _ = _session(root, runs=16, seed=3)
    d = diags["test_victim"]
    assert d.kind is OrderKind.POLLUTED
    assert sorted(_names(d.culprits)) == ["test_set_a", "test_set_b"]
    assert d.confirmed


def test_random_failure_is_not_blamed_on_order(make_project):
    root = make_project(
        {
            "tests/test_r.py": "import random\n\n"
            "def test_coin():\n    assert random.random() < 0.6\n\n" + FILLER,
        }
    )
    diags, _ = _session(root, runs=12, seed=5)
    assert diags["test_coin"].kind is OrderKind.NOT_ORDER
    assert diags["test_coin"].culprits == []


def test_cli_no_diagnose_and_json_diagnosis(make_project, monkeypatch, tmp_path):
    root = make_project(
        {
            "app.py": "cache = {}\n",
            "tests/test_a.py": "import app\n\n"
            "def test_victim():\n    assert app.cache == {}\n\n"
            "def test_polluter():\n    app.cache['k'] = 1\n\n" + FILLER,
        }
    )
    monkeypatch.chdir(root)
    out = tmp_path / "r.json"
    assert main(["run", "-n", "10", "-j", "4", "--seed", "1", "--json", str(out)]) == 1
    detail = {t["nodeid"].split("::")[-1]: t for t in json.loads(out.read_text())["tests_detail"]}
    assert detail["test_victim"]["diagnosis"]["kind"] == "order_polluted"
    assert detail["test_victim"]["diagnosis"]["repro_verified"] is True

    assert (
        main(["run", "-n", "10", "-j", "4", "--seed", "1", "--no-diagnose", "--json", str(out)])
        == 1
    )
    detail = {t["nodeid"].split("::")[-1]: t for t in json.loads(out.read_text())["tests_detail"]}
    assert detail["test_victim"]["diagnosis"] is None
