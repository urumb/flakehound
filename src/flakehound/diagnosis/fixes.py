"""Rule-based fix suggestions. No AI: each rule maps a kind of evidence to advice."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle at runtime
    from flakehound.diagnosis.order import OrderDiagnosis


def _short(nodeid: str) -> str:
    return nodeid.split("::")[-1]


def _global_fix(name: str, after: str) -> str:
    module, _, attr = name.rpartition(".")
    if after.startswith(("[", "{", "set(", "deque(")) or "(len " in after:
        return (
            f"reset `{name}` around each test, e.g. an autouse fixture that clears it "
            f"after the test (`yield` then `{name}.clear()`)"
        )
    if "Mock" in after:
        return (
            f"`{name}` is still patched: use `monkeypatch.setattr` or a `with patch(...)` "
            "block so the patch is undone"
        )
    return f"use `monkeypatch.setattr({module}, '{attr}', ...)` so `{name}` is restored"


_KIND_FIX = {
    "env": "use `monkeypatch.setenv`/`monkeypatch.delenv` so `{name}` is restored",
    "cwd": "use `monkeypatch.chdir(tmp_path)` so the working directory is restored",
    "file": "write files under `tmp_path` instead of the project folder (`{name}` is left behind)",
    "sys_path": "use `monkeypatch.syspath_prepend` so `sys.path` is restored",
    "logging": "restore logging config in teardown, or use the `caplog` fixture",
}


def suggest_fix(diag: OrderDiagnosis) -> str:
    from flakehound.diagnosis.order import OrderKind

    culprits = ", ".join(_short(c) for c in diag.culprits)
    if diag.kind is OrderKind.NEEDS_SETTER:
        return (
            f"{_short(diag.nodeid)} relies on state created by {culprits}; "
            "give it its own setup (a fixture) so it passes on its own"
        )
    if diag.kind is not OrderKind.POLLUTED:
        return ""
    if not diag.state_changes:
        return (
            f"make {culprits} clean up after itself; the leaked state is not in module "
            "globals, env vars, cwd or project files, so check fixtures with module/session "
            "scope, caches, databases or services it touches"
        )
    change = diag.state_changes[0]
    if change.kind in ("global", "class_attr"):
        advice = _global_fix(change.name, change.after)
    else:
        advice = _KIND_FIX.get(change.kind, "restore `{name}` in teardown").format(name=change.name)
    return f"in {culprits}: {advice}"
