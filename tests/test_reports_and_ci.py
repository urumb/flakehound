"""Markdown report, GitHub job summary, --fail-on, and pytest-xdist projects."""

import json

import pytest

from flakehound.cli import main
from flakehound.report.markdown import cell, code

ORDER = {
    "app.py": "cache = {}\n",
    "tests/test_a.py": "import app\n\n"
    "def test_victim():\n    assert app.cache == {}\n\n"
    "def test_polluter():\n    app.cache['k'] = 1\n\n"
    "def test_fine():\n    assert True\n",
}


def test_code_and_cell_escaping():
    assert code("a`b") == "``a`b``"
    assert code("`x`") == "`` `x` ``"
    assert cell("a|b\nc") == "a\\|b c"


def test_markdown_report(make_project, monkeypatch, tmp_path):
    secret = "tok-should-never-appear-42"
    monkeypatch.setenv("SOME_API_TOKEN", secret)
    files = dict(ORDER)
    files["tests/test_b.py"] = (
        "import os, random\n\n"
        "def test_leaky():\n    assert random.random() < 0.5, os.environ['SOME_API_TOKEN']\n"
    )
    root = make_project(files)
    monkeypatch.chdir(root)
    md = tmp_path / "report.md"
    assert main(["run", "-n", "8", "--seed", "3", "--no-probes", "--markdown", str(md)]) == 1
    text = md.read_text(encoding="utf-8")
    assert text.startswith("## flakehound report")
    assert "### Flaky tests" in text
    assert "| `tests/test_a.py::test_victim` |" in text
    assert "Order dependency (confirmed): polluted by `test_polluter`" in text
    assert "<details>" in text and "```bash" in text
    assert "(verified)" in text
    assert secret not in text


def test_github_step_summary_is_appended(make_project, monkeypatch, tmp_path):
    root = make_project(ORDER)
    monkeypatch.chdir(root)
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(["run", "-n", "6", "--seed", "3", "--no-probes"])
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier step\n")  # appended, never overwritten
    assert "## flakehound report" in text

    summary.write_text("", encoding="utf-8")
    main(["run", "-n", "6", "--seed", "3", "--no-probes", "--no-step-summary"])
    assert summary.read_text(encoding="utf-8") == ""


def test_fail_on(make_project, monkeypatch):
    root = make_project({**ORDER, "tests/test_broken.py": "def test_always():\n    assert False\n"})
    monkeypatch.chdir(root)
    base = ["run", "-n", "6", "--seed", "3", "--no-probes"]
    assert main(base) == 1  # flaky found
    assert main([*base, "--fail-on", "none"]) == 0
    assert main([*base, "--fail-on", "time-bomb,racy"]) == 0
    assert main([*base, "--fail-on", "broken"]) == 1
    with pytest.raises(SystemExit) as exc:
        main([*base, "--fail-on", "everything"])
    assert exc.value.code == 2


def test_project_with_xdist_in_addopts(make_project, monkeypatch, tmp_path):
    pytest.importorskip("xdist")
    root = make_project(ORDER)
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\naddopts = "-n 2"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    out = tmp_path / "r.json"
    assert main(["run", "-n", "8", "--seed", "3", "--no-probes", "--json", str(out)]) == 1
    detail = {t["nodeid"].split("::")[-1]: t for t in json.loads(out.read_text())["tests_detail"]}
    diag = detail["test_victim"]["diagnosis"]
    assert diag["kind"] == "order_polluted"
    assert diag["repro_verified"] is True
    assert "-n" in diag["repro"]  # repro also turns xdist off, so the order holds


def test_markdown_written_atomically(make_project, monkeypatch, tmp_path):
    root = make_project({"tests/test_ok.py": "def test():\n    assert True\n"})
    monkeypatch.chdir(root)
    md = tmp_path / "sub" / "r.md"
    assert main(["run", "-n", "2", "--no-probes", "--markdown", str(md)]) == 0
    assert "No problems found" in md.read_text(encoding="utf-8")
    assert not list(md.parent.glob("*.tmp"))


def test_no_problems_markdown_mentions_stable(make_project, monkeypatch, tmp_path):
    root = make_project({"tests/test_ok.py": "def test():\n    assert True\n"})
    monkeypatch.chdir(root)
    md = tmp_path / "r.md"
    main(["run", "-n", "2", "--no-probes", "--markdown", str(md)])
    assert "(1 stable)" in md.read_text(encoding="utf-8")
