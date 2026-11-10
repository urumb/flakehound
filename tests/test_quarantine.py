"""Quarantine file, pytest plugin and the `flakehound quarantine` commands."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from flakehound.cli import main
from flakehound.quarantine import Entry, Quarantine, QuarantineError, load, render, save

FLAKY = {
    "tests/test_q.py": "def test_bad():\n    assert False\n\n"
    "def test_param_ok():\n    assert True\n\n"
    "import pytest\n\n"
    "@pytest.mark.parametrize('x', [1, 2])\n"
    "def test_param(x):\n    assert x == 1\n",
}


def _pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rxs", *args],
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_roundtrip_and_matching(tmp_path):
    q = Quarantine(
        "xfail",
        [Entry("t.py::test_a", 'reason with "quotes" and ünïcode', "2026-11-10"), Entry("t.py::b")],
    )
    save(tmp_path, q)
    loaded = load(tmp_path)
    assert loaded is not None
    assert loaded.entries == sorted(q.entries, key=lambda e: e.id)
    assert loaded.find("t.py::b[1]") is not None  # all parametrizations
    assert loaded.find("t.py::bc") is None  # but not other tests with that prefix
    assert render(loaded) == render(q)


def test_missing_file_means_no_quarantine(tmp_path):
    assert load(tmp_path) is None


@pytest.mark.parametrize(
    "content",
    ["not = [valid", 'mode = "explode"\n', '[[test]]\nreason = "no id"\n'],
)
def test_invalid_files_are_rejected(tmp_path, content):
    (tmp_path / ".flakehound").mkdir()
    (tmp_path / ".flakehound" / "quarantine.toml").write_text(content, encoding="utf-8")
    with pytest.raises(QuarantineError):
        load(tmp_path)


def test_plugin_is_inert_without_file(make_project):
    root = make_project(FLAKY)
    assert _pytest(root).returncode == 1


def test_plugin_marks_quarantined_tests_xfail(make_project):
    root = make_project(FLAKY)
    save(root, Quarantine("xfail", [Entry("tests/test_q.py::test_bad", "known flaky")]))
    save(root, Quarantine("xfail", [*load(root).entries, Entry("tests/test_q.py::test_param")]))
    out = _pytest(root)
    assert out.returncode == 0, out.stdout
    assert "quarantined by flakehound: known flaky" in out.stdout
    header = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "flakehound: 2 quarantined test(s) (xfail)" in header.stdout  # not shown with -q
    assert "xpassed" in out.stdout  # test_param[1] passes: visible, not hidden

    # Escape hatch for one run.
    assert _pytest(root, "-p", "no:flakehound-quarantine").returncode == 1


def test_skip_mode(make_project):
    root = make_project(FLAKY)
    save(
        root,
        Quarantine(
            "skip", [Entry("tests/test_q.py::test_bad"), Entry("tests/test_q.py::test_param")]
        ),
    )
    out = _pytest(root)
    assert out.returncode == 0
    assert "skipped" in out.stdout


def test_invalid_file_is_a_clear_pytest_error(make_project):
    root = make_project(FLAKY)
    (root / ".flakehound").mkdir()
    (root / ".flakehound" / "quarantine.toml").write_text("oops = [", encoding="utf-8")
    out = _pytest(root)
    assert out.returncode != 0
    assert "flakehound quarantine" in out.stdout + out.stderr


def test_cli_add_needs_confirmation(make_project, monkeypatch):
    root = make_project(FLAKY)
    monkeypatch.chdir(root)
    # pytest's stdin is not a terminal: without --yes nothing is written.
    assert main(["quarantine", "add", "tests/test_q.py::test_bad"]) == 0
    assert load(root) is None
    assert main(["quarantine", "add", "tests/test_q.py::test_bad", "--reason", "x", "--yes"]) == 0
    assert load(root).ids() == ["tests/test_q.py::test_bad"]


def test_cli_remove_and_list(make_project, monkeypatch, capsys):
    root = make_project(FLAKY)
    monkeypatch.chdir(root)
    main(["quarantine", "add", "tests/test_q.py::test_bad", "tests/test_q.py::test_param", "-y"])
    main(["quarantine", "list"])
    assert "2 quarantined" in capsys.readouterr().out
    main(["quarantine", "remove", "tests/test_q.py::test_bad", "-y"])
    assert load(root).ids() == ["tests/test_q.py::test_param"]
    main(["quarantine", "remove", "--all", "-y"])
    assert load(root).entries == []


def test_cli_add_from_report_and_hunting_ignores_quarantine(make_project, monkeypatch, tmp_path):
    files = {
        "app.py": "cache = {}\n",
        "tests/test_a.py": "import app\n\n"
        "def test_victim():\n    assert app.cache == {}\n\n"
        "def test_polluter():\n    app.cache['k'] = 1\n",
    }
    root = make_project(files)
    monkeypatch.chdir(root)
    report = tmp_path / "r.json"
    main(["run", "-n", "8", "--seed", "3", "--no-probes", "--json", str(report)])
    assert main(["quarantine", "add", "--from-report", str(report), "-y"]) == 0
    q = load(root)
    assert q.ids() == ["tests/test_a.py::test_victim"]
    assert "order" in q.entries[0].reason

    # flakehound's own runs ignore the quarantine: the flaky test is still hunted.
    report2 = tmp_path / "r2.json"
    assert main(["run", "-n", "8", "--seed", "3", "--no-probes", "--json", str(report2)]) == 1
    data = json.loads(report2.read_text())
    assert data["summary"]["flaky"] == 1
