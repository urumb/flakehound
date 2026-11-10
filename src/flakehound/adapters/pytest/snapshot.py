"""Snapshot and diff process-global state around one test.

Used to name *what* a polluter test leaked: "test_add_item leaves
shop.cart = ['apple']" is far more useful than "test_add_item leaks state".

What we look at (cheap, read-only):
* module-level variables of the project's own modules (not site-packages),
  one level into plain objects and class attributes;
* ``os.environ``, the current directory, ``sys.path``;
* files in the project directory (name, size, mtime);
* the root logger's level and handlers, and ``logging.disable``.

Limits, stated honestly: state hidden in fixtures, closures, C extensions,
databases or external services is invisible here.

Every value is passed through :func:`redact` and truncated, and environment
variables with secret-sounding names never show their values.
"""

from __future__ import annotations

import logging
import os
import re
import reprlib
import sys
import sysconfig
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from flakehound.core.models import StateChange
from flakehound.report.redact import REDACTED, redact

MISSING = "<missing>"
_MAX_VALUE = 120
_MAX_ATTRS = 50
_MAX_FILES = 20_000
_MAX_CHANGES = 25
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "build",
    "dist",
    ".eggs",
}
_SKIP_MODULE_PREFIXES = ("flakehound", "_pytest", "pytest", "pluggy", "py.")
_SECRET_ENV_NAME = re.compile(r"(?i)(pass|secret|token|key|auth|credential|cookie|session)")

_repr = reprlib.Repr()
_repr.maxstring = 60
_repr.maxother = 60
_repr.maxlist = _repr.maxtuple = _repr.maxset = _repr.maxfrozenset = _repr.maxdeque = 8
_repr.maxdict = 8
_repr.maxlevel = 3


def safe_repr(value: object) -> str:
    try:
        text = _repr.repr(value)
    except Exception:  # a user __repr__ may raise anything
        text = f"<{type(value).__name__} (repr failed)>"
    try:
        size = len(value)  # type: ignore[arg-type]
    except Exception:
        size = None
    # Truncated containers can hide in-place changes: include the length.
    if size is not None and isinstance(value, (list, dict, set, tuple, frozenset)):
        text = f"{text} (len {size})" if size > _repr.maxlist else text
    return redact(text if len(text) <= _MAX_VALUE else text[: _MAX_VALUE - 3] + "...")


@dataclass
class Snapshot:
    values: dict[tuple[str, str], str] = field(default_factory=dict)  # (kind, name) -> repr
    files: dict[str, tuple[int, int]] = field(default_factory=dict)  # path -> (size, mtime)


def _library_paths() -> list[str]:
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    known = sysconfig.get_paths()
    for key in ("purelib", "platlib", "stdlib", "platstdlib"):
        if key in known:
            paths.add(known[key])
    return [os.path.normcase(os.path.abspath(p)) for p in paths if p]


def _is_project_module(name: str, module: types.ModuleType, root: str, libs: list[str]) -> bool:
    if name.startswith(_SKIP_MODULE_PREFIXES) or name == "__main__":
        return False
    file = getattr(module, "__file__", None)
    if not file:
        return False
    path = os.path.normcase(os.path.abspath(file))
    if not path.startswith(root):
        return False
    return not any(path.startswith(lib) for lib in libs)


def _plain_attrs(obj: object) -> Iterator[tuple[str, object]]:
    try:
        attrs = vars(obj)
    except TypeError:
        return
    for i, (key, value) in enumerate(list(attrs.items())):
        if i >= _MAX_ATTRS:
            break
        if key.startswith("__") or callable(value) or isinstance(value, types.ModuleType):
            continue
        yield key, value


def _module_state(root: Path) -> Iterator[tuple[str, str, str]]:
    root_s = os.path.normcase(str(root.resolve()))
    libs = _library_paths()
    for mod_name, module in list(sys.modules.items()):
        if module is None or not _is_project_module(mod_name, module, root_s, libs):
            continue
        for attr, value in list(vars(module).items()):
            if attr.startswith("__") or isinstance(value, types.ModuleType):
                continue
            if isinstance(value, type):
                if getattr(value, "__module__", None) != mod_name:
                    continue  # imported class: owned by another module
                for key, cval in _plain_attrs(value):
                    yield "class_attr", f"{mod_name}.{attr}.{key}", safe_repr(cval)
                continue
            if callable(value):
                continue  # functions, and objects like mocks are caught via repr below
            yield "global", f"{mod_name}.{attr}", safe_repr(value)
            # One level into plain objects: catches app.state.CURRENCY = "EUR".
            if not isinstance(value, (str, bytes, int, float, bool, list, dict, set, tuple)):
                for key, aval in _plain_attrs(value):
                    yield "global", f"{mod_name}.{attr}.{key}", safe_repr(aval)


def _files(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            path = Path(dirpath, name)
            try:
                st = path.stat()
            except OSError:
                continue
            out[path.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
            if len(out) >= _MAX_FILES:
                return out
    return out


def _env_value(name: str, value: str) -> str:
    if _SECRET_ENV_NAME.search(name):
        return REDACTED
    return safe_repr(value)


def take(root: Path) -> Snapshot:
    snap = Snapshot()
    v = snap.values
    for kind, name, rep in _module_state(root):
        v[(kind, name)] = rep
    for key, value in os.environ.items():
        v[("env", key)] = _env_value(key, value)
    try:
        v[("cwd", "current directory")] = redact(os.getcwd())
    except OSError:
        v[("cwd", "current directory")] = "<deleted>"
    v[("sys_path", "sys.path")] = safe_repr(list(sys.path))
    root_logger = logging.getLogger()
    v[("logging", "root logger level")] = logging.getLevelName(root_logger.level)
    v[("logging", "root logger handlers")] = safe_repr(
        [type(h).__name__ for h in root_logger.handlers]
    )
    v[("logging", "logging.disable")] = str(logging.root.manager.disable)
    snap.files = _files(root)
    return snap


def diff(before: Snapshot, after: Snapshot) -> list[StateChange]:
    changes: list[StateChange] = []
    for key in sorted(set(before.values) | set(after.values)):
        old = before.values.get(key, MISSING)
        new = after.values.get(key, MISSING)
        if old != new:
            changes.append(StateChange(key[0], key[1], old, new))
    for path in sorted(set(before.files) | set(after.files)):
        old_f = before.files.get(path)
        new_f = after.files.get(path)
        if old_f == new_f:
            continue
        if old_f is None:
            changes.append(StateChange("file", path, MISSING, "created"))
        elif new_f is None:
            changes.append(StateChange("file", path, "existed", "deleted"))
        else:
            changes.append(StateChange("file", path, "existed", "modified"))
    # Most useful first: globals, then env, then the rest.
    rank = {"global": 0, "class_attr": 1, "env": 2, "cwd": 3, "file": 4, "sys_path": 5}
    changes.sort(key=lambda c: (rank.get(c.kind, 9), c.name))
    return changes[:_MAX_CHANGES]
