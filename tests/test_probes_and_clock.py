"""Probe planning (pure) and the shifted clock / seed options (real pytest subprocesses)."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from flakehound.detectors.probes import make_probes
from flakehound.detectors.shake import plan_rechecks


def test_probes_relative_to_today():
    probes = {p.name: p for p in make_probes(date(2026, 11, 10))}
    assert probes["month_end"].clock == "2026-11-30T12:00:00"
    assert probes["year_end"].clock == "2026-12-31T23:59:58"
    assert probes["midnight"].clock == "2026-11-10T23:59:58"
    assert probes["leap_day"].clock == "2028-02-29T12:00:00"
    assert probes["tz_utc_plus_14"].env == {"TZ": "FHT-14"}  # POSIX sign is inverted
    assert all(p.label.startswith(("on ", "in ", "two ")) for p in probes.values())


def test_leap_day_edge_cases():
    assert {p.name: p for p in make_probes(date(2028, 2, 29))}["leap_day"].clock.startswith(
        "2028-02-29"
    )
    assert {p.name: p for p in make_probes(date(2028, 3, 1))}["leap_day"].clock.startswith(
        "2032-02-29"
    )


def test_rechecks_are_alone_and_reproducible():
    a = plan_rechecks(["t1", "t2"], master_seed=9, first_id=100)
    assert a == plan_rechecks(["t1", "t2"], master_seed=9, first_id=100)
    assert [s.order for s in a] == [["t1"]] * 4 + [["t2"]] * 4
    assert [s.run_id for s in a] == list(range(100, 108))
    assert len({s.hash_seed for s in a}) == 8


def _pytest(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    import os

    run_env = dict(os.environ)
    run_env.update(env or {})
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", *args],
        cwd=root,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return out.stdout + out.stderr


CLOCK_TEST = """
import datetime, json, time
from datetime import date, datetime as DT

def test_clock():
    info = {
        "today": date.today().isoformat(),
        "now": DT.now().isoformat(timespec="seconds"),
        "strftime": time.strftime("%Y-%m-%d"),
        "isinstance_ok": isinstance(DT.now(), datetime.datetime)
            and isinstance(datetime.datetime(2020, 1, 1), DT)
            and isinstance(date.today(), datetime.date),
        "monotonic_ok": time.monotonic() > 0,
    }
    print("CLOCK" + json.dumps(info))
"""


def test_clock_option_shifts_dates_but_keeps_types(make_project):
    root = make_project({"tests/test_clock.py": CLOCK_TEST})
    out = _pytest(
        root, "-p", "flakehound.adapters.pytest.plugin", "--flakehound-clock=2028-02-29T23:59:58"
    )
    info = json.loads(out.split("CLOCK", 1)[1].splitlines()[0])
    assert info["today"] == "2028-02-29"
    assert info["now"].startswith("2028-02-29T23:59:5")
    assert info["strftime"] == "2028-02-29"
    assert info["isinstance_ok"] and info["monotonic_ok"]


def test_clock_is_untouched_without_the_option(make_project):
    root = make_project({"tests/test_clock.py": CLOCK_TEST})
    out = _pytest(root, "-p", "flakehound.adapters.pytest.plugin")
    info = json.loads(out.split("CLOCK", 1)[1].splitlines()[0])
    assert info["today"] == date.today().isoformat()


def test_seed_option_is_deterministic_per_test(make_project):
    root = make_project(
        {"tests/test_r.py": "import random\n\ndef test_r():\n    print('R', random.random())\n"}
    )
    args = ("-p", "flakehound.adapters.pytest.plugin", "--flakehound-seed=42")
    first = [ln for ln in _pytest(root, *args).splitlines() if ln.startswith("R ")]
    second = [ln for ln in _pytest(root, *args).splitlines() if ln.startswith("R ")]
    other = [ln for ln in _pytest(root, args[0], args[1], "--flakehound-seed=43").splitlines()]
    assert first and first == second
    assert first[0] not in other


def test_bad_clock_value_is_a_usage_error(make_project):
    root = make_project({"tests/test_x.py": "def test():\n    pass\n"})
    out = _pytest(root, "-p", "flakehound.adapters.pytest.plugin", "--flakehound-clock=nope")
    assert "--flakehound-clock" in out
