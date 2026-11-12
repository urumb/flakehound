"""End-to-end environment diagnosis: seeds, nondeterminism and time bombs."""

import calendar
import json
from datetime import date
from pathlib import Path

import pytest

from flakehound.adapters.pytest.runner import PytestRunner
from flakehound.cli import main
from flakehound.core.procs import ProcessRegistry
from flakehound.diagnosis.env import EnvCause, classify_pair
from flakehound.session import run_session

FILLER = "\n".join(f"def test_filler_{i}():\n    assert True\n" for i in range(4))


def _session(root: Path, *, runs: int = 12, seed: int = 1, probe: bool = True):
    procs = ProcessRegistry()
    return run_session(
        PytestRunner(root, procs=procs, timeout=120),
        procs,
        runs=runs,
        workers=4,
        master_seed=seed,
        probe=probe,
    )


def _short(nodeid: str) -> str:
    return nodeid.split("::")[-1]


def test_random_and_hash_seed_are_told_apart(make_project):
    root = make_project(
        {
            "tests/test_seeds.py": "import random\n\n"
            "def test_coin():\n    assert random.random() < 0.5\n\n"
            "TAGS = {'aa', 'bb', 'cc', 'dd'}\n\n"
            "def test_set_order():\n"
            "    assert ','.join(TAGS) == ','.join(sorted(TAGS))\n\n" + FILLER,
        }
    )
    report = _session(root, runs=16, seed=3, probe=False)
    env = {_short(k): d for k, d in report.env_diagnoses.items()}

    coin = env["test_coin"]
    assert coin.cause is EnvCause.RANDOM_SEED
    assert coin.confirmed and coin.repro_verified is True
    assert any(a.startswith("--flakehound-seed=") for a in coin.repro)

    order = env["test_set_order"]
    assert order.cause is EnvCause.HASH_SEED
    assert order.confirmed
    assert "PYTHONHASHSEED" in order.repro_env
    assert "sorted" in order.fix


@pytest.mark.parametrize(
    ("fail_seeds", "pass_seeds", "swap_hash", "swap_random", "expected"),
    [
        ((True, True), False, True, False, EnvCause.RANDOM_SEED),
        ((True, True), False, False, True, EnvCause.HASH_SEED),
        ((True, True), False, False, False, EnvCause.SEEDS_COMBINED),
        ((True, True), False, True, True, EnvCause.INCONCLUSIVE),
        ((True, False), False, True, False, EnvCause.NONDETERMINISTIC),  # same seeds differ
        ((True, True), True, True, False, EnvCause.NONDETERMINISTIC),  # passing seeds fail
    ],
)
def test_classify_pair(fail_seeds, pass_seeds, swap_hash, swap_random, expected):
    assert classify_pair(fail_seeds, pass_seeds, swap_hash, swap_random) is expected


DATE_BOMBS = """
from datetime import date, datetime, timedelta, timezone

def test_month_end():
    assert (date.today() + timedelta(days=1)).month == date.today().month

def test_leap():
    t = date.today()
    assert t.replace(year=t.year + 1) > t

def test_tz():
    assert datetime.now().date() == datetime.now(timezone.utc).date()

def test_fine():
    assert date.today().year > 2000
"""


def _today_is_a_danger_day() -> bool:
    t = date.today()
    return t.day == calendar.monthrange(t.year, t.month)[1] or (t.month, t.day) == (2, 29)


# These tests check time bombs, so on a real danger day they would "go off" in the
# baseline run too. Skip rather than make flakehound's own CI a time bomb.
danger_day = pytest.mark.skipif(_today_is_a_danger_day(), reason="today is a month end or leap day")


@danger_day
def test_time_bombs_found_and_verified(make_project):
    root = make_project({"tests/test_dates.py": DATE_BOMBS})
    report = _session(root, runs=4, seed=1)
    bombs = {_short(d.nodeid): d for d in report.latent}
    assert "test_fine" not in bombs

    month = bombs["test_month_end"]
    assert month.cause is EnvCause.CLOCK and month.latent
    assert month.confirmed and month.repro_verified is True
    assert any(a.startswith("--flakehound-clock=") for a in month.repro)

    leap = bombs["test_leap"]
    assert leap.cause is EnvCause.CLOCK
    assert "leap day" in leap.condition

    tz = bombs["test_tz"]
    assert tz.cause is EnvCause.TIMEZONE
    assert tz.confirmed
    assert "TZ" in tz.repro_env


@danger_day
def test_no_probes_flag_and_json(make_project, monkeypatch, tmp_path):
    root = make_project({"tests/test_dates.py": DATE_BOMBS})
    monkeypatch.chdir(root)
    out = tmp_path / "r.json"
    assert main(["run", "-n", "3", "-j", "4", "--seed", "1", "--json", str(out)]) == 1
    data = json.loads(out.read_text())
    assert data["schema_version"] == 3
    assert {_short(b["nodeid"]) for b in data["latent"]} >= {"test_month_end", "test_tz"}
    assert len(data["probes"]) == 8

    assert (
        main(["run", "-n", "3", "-j", "4", "--seed", "1", "--no-probes", "--json", str(out)]) == 0
    )
    data = json.loads(out.read_text())
    assert data["latent"] == [] and data["probes"] == []


TZ_SETTING = """
import os

def test_needs_tz_unset():
    # Breaks whenever TZ is set at all, whatever the offset (like a cache keyed on TZ).
    assert "TZ" not in os.environ
"""


def test_tz_variable_dependence_is_not_called_a_timezone_bug(make_project, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    root = make_project({"tests/test_tzvar.py": TZ_SETTING})
    report = _session(root, runs=2, seed=1)
    [diag] = report.latent
    assert diag.cause is EnvCause.TIMEZONE
    assert not diag.confirmed  # reported as suspected, so it doesn't fail the build
    assert "TZ environment variable is set" in diag.condition
    assert "own offset" in diag.note
    assert "monkeypatch.delenv" in diag.fix
    assert any("own UTC offset" in e for e in diag.evidence)


def test_posix_tz_strings():
    from datetime import timedelta

    from flakehound.detectors.probes import local_offset_probe, posix_tz

    assert posix_tz(timedelta(hours=14)) == "FHT-14"
    assert posix_tz(timedelta(hours=-12)) == "FHT+12"
    assert posix_tz(timedelta(hours=5, minutes=30)) == "FHT-5:30"
    assert posix_tz(timedelta(hours=-3, minutes=-30)) == "FHT+3:30"
    assert posix_tz(timedelta(0)) == "FHT+0"
    assert local_offset_probe(timedelta(hours=1)).env == {"TZ": "FHT-1"}


ONE_OFF = """
import os
from pathlib import Path

MARK = Path(__file__).with_name("failed-once.txt")

def test_one_off_failure():
    # Fails the first time it runs with TZ set, then never again (like a timeout
    # on a busy machine): that is not a time bomb.
    if os.environ.get("TZ") and not MARK.exists():
        MARK.write_text("x")
        raise AssertionError("one-off")
"""


def test_probe_failure_that_never_repeats_is_not_reported(make_project, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    root = make_project({"tests/test_once.py": ONE_OFF})
    report = _session(root, runs=2, seed=1)
    from flakehound.diagnosis.common import failed

    victim = "tests/test_once.py::test_one_off_failure"
    assert any(failed(r, victim) for r in report.probe_results)  # it did fail once
    assert report.latent == []
