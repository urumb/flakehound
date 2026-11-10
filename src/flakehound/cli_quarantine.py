"""``flakehound quarantine list|add|remove``: edit the quarantine file safely.

Every change is shown as a diff first and needs a yes (or ``--yes``).
Without a terminal and without ``--yes`` nothing is written.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from flakehound.quarantine import Entry, Quarantine, QuarantineError, load, path_for, render, save

EXIT_OK = 0
EXIT_USAGE = 2


def add_parser(sub: Any) -> None:
    q = sub.add_parser(
        "quarantine",
        help="keep known-flaky tests from failing the build (.flakehound/quarantine.toml)",
        description=(
            "Quarantined tests still run, but pytest treats them as non-strict xfail (or skip), "
            "so they don't fail the build. The list is one file, .flakehound/quarantine.toml: "
            "commit it, review it, and revert it like any other change."
        ),
    )
    q.add_argument("--root", type=Path, default=Path("."), help="project root (default: .)")
    qsub = q.add_subparsers(dest="action", required=True)
    qsub.add_parser("list", help="show quarantined tests")
    add = qsub.add_parser("add", help="quarantine tests")
    add.add_argument("tests", nargs="*", help="test ids, e.g. tests/test_x.py::test_y")
    add.add_argument("--reason", default="", help="why (shown in pytest output)")
    add.add_argument(
        "--from-report",
        type=Path,
        default=None,
        metavar="JSON",
        help="add every flaky test and confirmed time bomb/racy test from a flakehound JSON report",
    )
    add.add_argument("--mode", choices=("xfail", "skip"), default=None, help="xfail or skip")
    add.add_argument("-y", "--yes", action="store_true", help="write without asking")
    rm = qsub.add_parser("remove", help="take tests out of quarantine")
    rm.add_argument("tests", nargs="*", help="test ids")
    rm.add_argument("--all", action="store_true", help="remove every entry")
    rm.add_argument("-y", "--yes", action="store_true", help="write without asking")


def _from_report(path: Path) -> list[Entry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    entries: list[Entry] = []
    for t in data.get("tests_detail", []):
        if t.get("verdict") != "flaky":
            continue
        diag = t.get("env_diagnosis") or t.get("diagnosis") or {}
        cause = str(diag.get("cause") or diag.get("kind") or "flaky").replace("_", " ")
        entries.append(Entry(t["nodeid"], f"flaky ({cause}), found by flakehound", today))
    for item in data.get("latent", []):
        if item.get("confirmed"):
            reason = f"{item.get('cause', 'latent')} issue: fails {item.get('condition', '')}"
            entries.append(Entry(item["nodeid"], reason.strip(), today))
    return entries


def _confirm(console: Console, before: str, after: str, path: Path, assume_yes: bool) -> bool:
    diff = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), str(path), str(path), lineterm=""
        )
    )
    if not diff:
        console.print("nothing to change")
        return False
    for line in diff:
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
        console.print(f"[{style}]{escape(line)}[/]", highlight=False)
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        console.print("[yellow]not writing: re-run with --yes to apply this change[/]")
        return False
    try:
        answer = input(f"Write these changes to {path}? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # No usable input (some shells report a terminal that isn't one): safe default.
        console.print("\n[yellow]not writing: no answer (use --yes to apply)[/]")
        return False
    return answer in ("y", "yes")


def run(args: argparse.Namespace, console: Console) -> int:
    root: Path = args.root
    path = path_for(root)
    try:
        current = load(root)
    except QuarantineError as exc:
        console.print(f"[bold red]error:[/] {escape(str(exc))}", highlight=False)
        return EXIT_USAGE
    q = current or Quarantine()
    before = render(current) if current else ""

    if args.action == "list":
        if not q.entries:
            console.print("no quarantined tests")
            return EXIT_OK
        console.print(f"{len(q.entries)} quarantined test(s), mode {q.mode} ({path}):")
        for e in q.entries:
            extra = f" [dim]{escape(e.reason)}[/]" if e.reason else ""
            console.print(f"  {escape(e.id)}{extra}", highlight=False)
        return EXIT_OK

    if args.action == "add":
        new: list[Entry] = [Entry(t, args.reason, date.today().isoformat()) for t in args.tests]
        if args.from_report is not None:
            try:
                new += _from_report(args.from_report)
            except (OSError, ValueError, KeyError) as exc:
                console.print(f"[bold red]error:[/] cannot read report: {escape(str(exc))}")
                return EXIT_USAGE
        if not new and args.mode is None:
            console.print("[yellow]nothing to add: give test ids or --from-report[/]")
            return EXIT_USAGE
        known = set(q.ids())
        for e in new:
            if e.id not in known:
                q.entries.append(e)
                known.add(e.id)
        if args.mode is not None:
            q.mode = args.mode
    else:  # remove
        if args.all:
            q.entries = []
        else:
            unknown = [t for t in args.tests if t not in q.ids()]
            for t in unknown:
                console.print(f"[yellow]not quarantined: {escape(t)}[/]", highlight=False)
            q.entries = [e for e in q.entries if e.id not in set(args.tests)]

    after = render(q)
    if not _confirm(console, before, after, path, args.yes):
        return EXIT_OK
    save(root, q)
    console.print(
        f"[green]wrote {path}[/] ({len(q.entries)} quarantined). Commit it to share; "
        "pytest applies it automatically while flakehound is installed.",
        highlight=False,
    )
    return EXIT_OK
