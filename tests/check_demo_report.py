"""CI check: the demo report must contain the expected diagnoses.

Usage: python tests/check_demo_report.py demo/report.json
"""

from __future__ import annotations

import calendar
import json
import sys
from datetime import date
from typing import Any

ORDER = {
    "test_cart_starts_empty": ("order_polluted", "test_add_item"),
    "test_prices_default_to_usd": ("order_polluted", "test_euro_store"),
    "test_apples_in_stock": ("order_needs_setter", "test_restock_apples"),
}
SEEDS = {
    "test_tags_render_alphabetically": "hash_seed",
    "test_lucky_discount_is_small": "random_seed",
}
TIME_BOMBS = {
    "test_coupon_valid_until_tomorrow": "clock",
    "test_anniversary_next_year": "clock",
    "test_order_date_matches_utc": "timezone",
}


def _danger_day() -> bool:
    t = date.today()
    return t.day == calendar.monthrange(t.year, t.month)[1] or (t.month, t.day) == (2, 29)


def _check_order(tests: dict[str, Any], problems: list[str]) -> None:
    for name, (kind, culprit) in ORDER.items():
        diag = (tests.get(name) or {}).get("diagnosis") or {}
        culprits = [c.split("::")[-1] for c in diag.get("culprits", [])]
        if diag.get("kind") != kind or culprits != [culprit] or not diag.get("confirmed"):
            problems.append(f"{name}: expected {kind} by {culprit}, got {diag or 'nothing'}")
        elif diag.get("repro_verified") is not True:
            problems.append(f"{name}: repro command was not verified")


def _check_seeds(tests: dict[str, Any], problems: list[str]) -> None:
    for name, cause in SEEDS.items():
        test = tests.get(name) or {}
        if test.get("verdict") != "flaky":
            # A ~20% failure rate can, rarely, pass all runs: not a diagnosis bug.
            print(f"note: {name} was not flaky in this session ({test.get('verdict')})")
            continue
        env = test.get("env_diagnosis") or {}
        if env.get("cause") != cause or env.get("repro_verified") is not True:
            problems.append(f"{name}: expected verified {cause}, got {env or 'nothing'}")


def _check_bombs(report: dict[str, Any], problems: list[str]) -> None:
    if _danger_day():
        print("note: today is a danger day, the date time bombs go off for real; skipping")
        return
    bombs = {b["nodeid"].split("::")[-1]: b for b in report["time_bombs"]}
    for name, cause in TIME_BOMBS.items():
        bomb = bombs.get(name) or {}
        if bomb.get("cause") != cause or bomb.get("repro_verified") is not True:
            problems.append(f"{name}: expected verified {cause} time bomb, got {bomb or 'nothing'}")


def main(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    tests = {t["nodeid"].split("::")[-1]: t for t in report["tests_detail"]}
    problems: list[str] = []
    _check_order(tests, problems)
    _check_seeds(tests, problems)
    _check_bombs(report, problems)
    for p in problems:
        print("FAIL", p)
    if not problems:
        print("ok: order, seed and time bomb diagnoses correct, repros verified")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
