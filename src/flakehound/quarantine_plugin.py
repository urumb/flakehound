"""pytest plugin that applies ``.flakehound/quarantine.toml``.

Registered as the ``flakehound-quarantine`` pytest plugin. It does nothing at
all unless the quarantine file exists in the project root, so installing
flakehound does not change your test runs by itself. Disable it for one run
with ``pytest -p no:flakehound-quarantine``. flakehound's own hunting runs
always disable it, so quarantined tests keep being investigated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flakehound.quarantine import Quarantine, QuarantineError, load

_KEY = pytest.StashKey[Quarantine | None]()


def pytest_configure(config: pytest.Config) -> None:
    try:
        config.stash[_KEY] = load(Path(str(config.rootpath)))
    except QuarantineError as exc:
        raise pytest.UsageError(f"flakehound quarantine: {exc}") from exc


def pytest_report_header(config: pytest.Config) -> str | None:
    q = config.stash.get(_KEY, None)
    if not q or not q.entries:
        return None
    count = len(q.entries)
    return f"flakehound: {count} quarantined test(s) ({q.mode}), see .flakehound/quarantine.toml"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    q = config.stash.get(_KEY, None)
    if not q or not q.entries:
        return
    for item in items:
        entry = q.find(item.nodeid)
        if entry is None:
            continue
        reason = f"quarantined by flakehound: {entry.reason or 'flaky'}"
        if q.mode == "skip":
            item.add_marker(pytest.mark.skip(reason=reason))
        else:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
