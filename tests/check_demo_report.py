"""CI check: the demo report must contain the expected diagnoses.

Usage: python tests/check_demo_report.py demo/report.json
"""

from __future__ import annotations

import json
import sys

EXPECTED = {
    "test_cart_starts_empty": ("order_polluted", "test_add_item"),
    "test_prices_default_to_usd": ("order_polluted", "test_euro_store"),
    "test_apples_in_stock": ("order_needs_setter", "test_restock_apples"),
}


def main(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    tests = {t["nodeid"].split("::")[-1]: t for t in report["tests_detail"]}
    problems = []
    for name, (kind, culprit) in EXPECTED.items():
        diag = (tests.get(name) or {}).get("diagnosis") or {}
        culprits = [c.split("::")[-1] for c in diag.get("culprits", [])]
        if diag.get("kind") != kind or culprits != [culprit] or not diag.get("confirmed"):
            problems.append(f"{name}: expected {kind} by {culprit}, got {diag or 'nothing'}")
        elif diag.get("repro_verified") is not True:
            problems.append(f"{name}: repro command was not verified")
    for p in problems:
        print("FAIL", p)
    if not problems:
        print(f"ok: {len(EXPECTED)} order diagnoses correct, repros verified")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
