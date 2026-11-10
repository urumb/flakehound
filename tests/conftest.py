from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def make_project(tmp_path: Path) -> Callable[[dict[str, str]], Path]:
    """Create a throwaway pytest project from {relative path: source} pairs."""

    def _make(files: dict[str, str]) -> Path:
        root = tmp_path / "project"
        (root / "tests").mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n',
            encoding="utf-8",
        )
        for rel, src in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(src), encoding="utf-8")
        return root

    return _make
