"""End-to-end: real pytest subprocesses on generated projects."""

import json
from pathlib import Path

import pytest

from flakehound.adapters.base import CollectionError
from flakehound.adapters.pytest.runner import PytestRunner
from flakehound.cli import EXIT_FLAKY, EXIT_OK, EXIT_USAGE, main
from flakehound.core.models import RunSpec
from flakehound.core.procs import ProcessRegistry
from flakehound.detectors.shake import Verdict
from flakehound.session import run_session

ORDER_DEPENDENT = {
    "shared.py": "items = []\n",
    "tests/test_a.py": """
        import shared

        def test_starts_empty():
            assert shared.items == []

        def test_adds():
            shared.items.append(1)

        def test_stable():
            assert True
    """,
}


def test_collect_and_run_in_given_order(make_project, tmp_path: Path):
    root = make_project(ORDER_DEPENDENT)
    runner = PytestRunner(root, procs=ProcessRegistry(), timeout=120)
    ids = runner.collect(tmp_path)
    assert ids == [
        "tests/test_a.py::test_starts_empty",
        "tests/test_a.py::test_adds",
        "tests/test_a.py::test_stable",
    ]
    bad_order = [ids[1], ids[0]]  # polluter first, and test_stable left out
    result = runner.run(RunSpec(run_id=0, order=bad_order, seed=1), tmp_path)
    assert not result.crashed
    assert [r.nodeid for r in result.results] == bad_order
    assert result.outcome_of(ids[0]).is_failure
    assert result.outcome_of(ids[2]) is None


def test_session_finds_order_dependent_flake(make_project):
    root = make_project(ORDER_DEPENDENT)
    procs = ProcessRegistry()
    report = run_session(
        PytestRunner(root, procs=procs, timeout=120), procs, runs=8, workers=4, master_seed=3
    )
    verdicts = {s.nodeid.split("::")[1]: s.verdict for s in report.stats}
    assert verdicts == {
        "test_starts_empty": Verdict.FLAKY,
        "test_adds": Verdict.STABLE,
        "test_stable": Verdict.STABLE,
    }


def test_collection_error_is_reported(make_project, tmp_path: Path):
    root = make_project({"tests/test_bad.py": "def test(:\n"})
    with pytest.raises(CollectionError):
        PytestRunner(root, procs=ProcessRegistry(), timeout=120).collect(tmp_path)


def test_timeout_is_reported_as_crash(make_project, tmp_path: Path):
    root = make_project({"tests/test_hang.py": "import time\ndef test():\n    time.sleep(60)\n"})
    runner = PytestRunner(root, procs=ProcessRegistry(), timeout=3)
    result = runner.run(RunSpec(0, ["tests/test_hang.py::test"], 1), tmp_path)
    assert result.crashed
    assert "timed out" in result.error


def test_cli_exit_codes_and_json_has_no_secrets(make_project, monkeypatch, tmp_path: Path):
    secret = "super-secret-value-123"
    monkeypatch.setenv("DEMO_API_TOKEN", secret)
    files = dict(ORDER_DEPENDENT)
    files["tests/test_leak.py"] = """
        import os, random

        def test_leaks_secret_in_failure():
            assert random.random() < 0.5, os.environ["DEMO_API_TOKEN"]
    """
    root = make_project(files)
    monkeypatch.chdir(root)
    out = tmp_path / "report.json"
    code = main(["run", "-n", "10", "-j", "4", "--seed", "5", "--json", str(out)])
    assert code == EXIT_FLAKY
    text = out.read_text(encoding="utf-8")
    assert secret not in text
    data = json.loads(text)
    assert data["schema_version"] == 2
    assert data["summary"]["flaky"] >= 1
    assert "DEMO_API_TOKEN" not in text  # we never dump the environment


def test_cli_ok_and_usage_exit_codes(make_project, monkeypatch):
    root = make_project({"tests/test_ok.py": "def test():\n    assert True\n"})
    monkeypatch.chdir(root)
    assert main(["run", "-n", "2"]) == EXIT_OK
    assert main(["run", "-n", "2", "tests/does_not_exist.py"]) == EXIT_USAGE
