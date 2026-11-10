"""Path helpers shared by the worker hooks.

Paths are compared after resolving symlinks: on macOS the temp folder
``/var/...`` is a symlink to ``/private/var/...``, so a test file's path and the
project root can look different while being the same place.
"""

from __future__ import annotations

import functools
import os
import sys
import sysconfig


@functools.lru_cache(maxsize=4096)
def real(path: str) -> str:
    """Absolute path with symlinks resolved (keeps the original letter case)."""
    return os.path.realpath(path)


@functools.lru_cache(maxsize=4096)
def canon(path: str) -> str:
    """Comparable form: resolved and case-normalized (lowercase on Windows)."""
    return os.path.normcase(real(path))


def is_under(path: str, root: str) -> bool:
    """True if ``path`` is ``root`` or inside it. Both must be ``canon()`` forms."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


@functools.lru_cache(maxsize=1)
def library_roots() -> tuple[str, ...]:
    """Where installed packages and the standard library live (canon form)."""
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    known = sysconfig.get_paths()
    for key in ("purelib", "platlib", "stdlib", "platstdlib"):
        if key in known:
            paths.add(known[key])
    return tuple(canon(p) for p in paths if p)


# flakehound's own package directory: never treated as project code.
OWN_PACKAGE = canon(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def is_project_file(file: str, root: str) -> bool:
    """``file`` belongs to the user's project (``root`` in canon form)."""
    path = canon(file)
    if not is_under(path, root) or is_under(path, OWN_PACKAGE):
        return False
    return not any(is_under(path, lib) for lib in library_roots())
