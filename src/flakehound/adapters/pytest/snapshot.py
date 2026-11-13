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
import types
import unittest
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from flakehound.adapters.pytest.paths import canon, is_project_file
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
# Set and cleared by pytest itself around every test: never "leaked state".
_IGNORED_ENV = {"PYTEST_CURRENT_TEST"}
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
    # Names whose appearance is just a cache filling in (see _cache_names); a change
    # from "missing" (or None, for a lazy backing field) to a value is not a leak.
    caches: set[str] = field(default_factory=set)


def _is_project_module(name: str, module: types.ModuleType, root: str) -> bool:
    if name.startswith(_SKIP_MODULE_PREFIXES) or name == "__main__":
        return False
    file = getattr(module, "__file__", None)
    return bool(file) and is_project_file(str(file), root)


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


def _is_cached_property(cls: type, name: str) -> bool:
    attr = None
    for klass in getattr(cls, "__mro__", ()):
        if name in vars(klass):
            attr = vars(klass)[name]
            break
    return attr is not None and "cached_property" in type(attr).__name__


def _is_lazy_property(cls: type, name: str) -> bool:
    for klass in getattr(cls, "__mro__", ()):
        if name in vars(klass):
            attr = vars(klass)[name]
            return isinstance(attr, property) or "cached_property" in type(attr).__name__
    return False


def _cache_names(obj: object, prefix: str) -> Iterator[str]:
    """Attributes of ``obj`` that are caches rather than state a test could leak.

    * ``functools.cached_property`` (or Django's) values;
    * ``_x`` backing fields of a ``property``/``cached_property`` named ``x``
      (lazy initialisation: ``None`` until first use);
    * on objects with a custom ``__getattr__`` (Django and DRF settings), values
      that appear in the instance: they are copied there on first access. A value
      set there by a test for the very first time is missed (stated limit); a
      change to an existing value is still reported.
    """
    cls = type(obj)
    custom_getattr = getattr(cls, "__getattr__", None)
    try:
        attrs = list(vars(obj).items())[:_MAX_ATTRS]
    except TypeError:
        return
    for key, _value in attrs:
        backing_field = key.startswith("_") and not key.startswith("__")
        if _is_cached_property(cls, key) or (backing_field and _is_lazy_property(cls, key[1:])):
            yield f"{prefix}.{key}"
        elif custom_getattr is not None:
            # Never call __getattr__ here: lookups like these write to the object,
            # and a snapshot must not change anything. Values appearing out of
            # nowhere are treated as cache fills; changes to existing ones still show.
            yield f"{prefix}.{key}"


def _module_state(root: Path, caches: set[str] | None = None) -> Iterator[tuple[str, str, str]]:
    root_s = canon(str(root))
    seen: set[int] = set()  # objects imported under several names are looked into once
    for mod_name, module in list(sys.modules.items()):
        if module is None or not _is_project_module(mod_name, module, root_s):
            continue
        for attr, value in list(vars(module).items()):
            if attr.startswith("__") or isinstance(value, types.ModuleType):
                continue
            if isinstance(value, type):
                if getattr(value, "__module__", None) != mod_name:
                    continue  # imported class: owned by another module
                if issubclass(value, unittest.TestCase):
                    continue  # setUpClass bookkeeping (client, atomics...), not shared state
                for key, cval in _plain_attrs(value):
                    yield "class_attr", f"{mod_name}.{attr}.{key}", safe_repr(cval)
                continue
            if callable(value):
                continue  # functions, and objects like mocks are caught via repr below
            yield "global", f"{mod_name}.{attr}", safe_repr(value)
            # One level into plain objects: catches app.state.CURRENCY = "EUR".
            if not isinstance(value, (str, bytes, int, float, bool, list, dict, set, tuple)):
                if id(value) in seen:
                    continue
                seen.add(id(value))
                prefix = f"{mod_name}.{attr}"
                if caches is not None:
                    caches.update(_cache_names(value, prefix))
                for key, aval in _plain_attrs(value):
                    yield "global", f"{prefix}.{key}", safe_repr(aval)


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
    for kind, name, rep in _module_state(root, snap.caches):
        v[(kind, name)] = rep
    for key, value in os.environ.items():
        if key not in _IGNORED_ENV:
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


def _is_private_counter(name: str, old: str, new: str) -> bool:
    """A private integer that only went up: an id/creation counter, not a leak."""
    if not name.rpartition(".")[2].startswith("_"):
        return False
    try:
        return int(new) > int(old)
    except ValueError:
        return False


def diff(before: Snapshot, after: Snapshot) -> list[StateChange]:
    changes: list[StateChange] = []
    caches = before.caches | after.caches
    for key in sorted(set(before.values) | set(after.values)):
        old = before.values.get(key, MISSING)
        new = after.values.get(key, MISSING)
        if old == new:
            continue
        if key[1] in caches and old in (MISSING, "None"):
            continue  # a cache filling in, not a leak
        if _is_private_counter(key[1], old, new):
            continue  # e.g. Field._creation_counter: 166 -> 171 (counts objects made)
        changes.append(StateChange(key[0], key[1], old, new))
    # If a test replaced a whole object (e.g. settings swapped for a mock), the old
    # object's attributes "disappearing" is noise; real value changes stay.
    replaced = {
        c.name for c in changes if c.kind == "global" and MISSING not in (c.before, c.after)
    }
    changes = [
        c
        for c in changes
        if not (
            c.kind == "global"
            and MISSING in (c.before, c.after)
            and c.name.rpartition(".")[0] in replaced
        )
    ]
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
