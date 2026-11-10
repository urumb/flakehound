"""Quarantine: let known-flaky tests keep running without failing the build.

The list lives in one reviewable file, ``.flakehound/quarantine.toml``, meant to
be committed. flakehound never edits test files. The pytest plugin in
:mod:`flakehound.quarantine_plugin` marks listed tests as non-strict ``xfail``
(they still run, and show as XPASS once fixed) or ``skip``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 in CI
    import tomli as tomllib  # type: ignore[import-not-found,no-redef,unused-ignore]

FILE = Path(".flakehound") / "quarantine.toml"
MODES = ("xfail", "skip")
_HEADER = """\
# Tests quarantined by flakehound (https://github.com/urumb/flakehound).
# pytest still runs them, but their failures don't fail the build:
#   mode = "xfail"  -> marked xfail (non-strict); shows XPASS once the test is fixed
#   mode = "skip"   -> not run at all
# Remove an entry (or run `flakehound quarantine remove <test>`) once it's fixed.
"""


class QuarantineError(ValueError):
    """The quarantine file exists but is not valid."""


@dataclass(frozen=True)
class Entry:
    id: str
    reason: str = ""
    added: str = ""

    def matches(self, nodeid: str) -> bool:
        """Exact match, or all parametrizations of a test (``test_x`` matches ``test_x[1]``)."""
        return nodeid == self.id or nodeid.split("[", 1)[0] == self.id


@dataclass
class Quarantine:
    mode: str = "xfail"
    entries: list[Entry] = field(default_factory=list)

    def find(self, nodeid: str) -> Entry | None:
        return next((e for e in self.entries if e.matches(nodeid)), None)

    def ids(self) -> list[str]:
        return [e.id for e in self.entries]


def path_for(root: Path) -> Path:
    return root / FILE


def load(root: Path) -> Quarantine | None:
    """The project's quarantine, or None if there is no quarantine file."""
    path = path_for(root)
    if not path.is_file():
        return None
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise QuarantineError(f"{path}: not valid TOML: {exc}") from exc
    mode = data.get("mode", "xfail")
    if mode not in MODES:
        raise QuarantineError(f"{path}: mode must be one of {', '.join(MODES)}, got {mode!r}")
    tests = data.get("test", [])
    if not isinstance(tests, list):
        raise QuarantineError(f"{path}: [[test]] entries expected")
    entries = []
    for i, item in enumerate(tests):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise QuarantineError(f'{path}: test entry {i + 1} needs an id = "..."')
        entries.append(
            Entry(str(item["id"]), str(item.get("reason", "")), str(item.get("added", "")))
        )
    return Quarantine(mode=str(mode), entries=entries)


def _toml_str(value: str) -> str:
    # JSON string escaping (ASCII only) is valid TOML basic-string syntax.
    return json.dumps(value, ensure_ascii=True)


def render(q: Quarantine) -> str:
    lines = [_HEADER, "version = 1", f"mode = {_toml_str(q.mode)}", ""]
    for e in sorted(q.entries, key=lambda e: e.id):
        lines += ["[[test]]", f"id = {_toml_str(e.id)}"]
        if e.reason:
            lines.append(f"reason = {_toml_str(e.reason)}")
        if e.added:
            lines.append(f"added = {_toml_str(e.added)}")
        lines.append("")
    return "\n".join(lines)


def save(root: Path, q: Quarantine) -> Path:
    path = path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(render(q), encoding="utf-8")
    tmp.replace(path)
    return path
