"""Command line interface: ``flakehound run``."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from flakehound import __version__, cli_quarantine
from flakehound.adapters.base import CollectionError
from flakehound.adapters.pytest.runner import PytestRunner
from flakehound.core.models import RunResult
from flakehound.core.procs import ProcessRegistry
from flakehound.detectors.shake import Verdict
from flakehound.report import terminal
from flakehound.report.findings import TIMING_CAUSES
from flakehound.report.json_report import write_json
from flakehound.report.markdown import write_markdown
from flakehound.session import Report

EXIT_OK = 0
EXIT_FLAKY = 1  # problems found (see --fail-on)
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


FAIL_ON_CHOICES = ("flaky", "time-bomb", "racy", "broken", "suspected", "none")
DEFAULT_FAIL_ON = ("flaky", "time-bomb", "racy")


def _fail_on(value: str) -> tuple[str, ...]:
    kinds = tuple(k.strip() for k in value.split(",") if k.strip())
    unknown = [k for k in kinds if k not in FAIL_ON_CHOICES]
    if unknown or not kinds:
        raise argparse.ArgumentTypeError(
            f"unknown kind(s) {', '.join(unknown) or '(empty)'}; choose from "
            + ", ".join(FAIL_ON_CHOICES)
        )
    return kinds


def should_fail(report: Report, fail_on: Sequence[str]) -> bool:
    """Exit code policy. By default only what was observed or proven fails a build:
    flaky tests (they did flip) and *confirmed* time bombs / racy tests."""
    if "none" in fail_on:
        return False
    include_suspected = "suspected" in fail_on
    for d in report.latent:
        kind = "racy" if d.cause in TIMING_CAUSES else "time-bomb"
        if kind in fail_on and (d.confirmed or include_suspected):
            return True
    if "flaky" in fail_on and report.by_verdict(Verdict.FLAKY):
        return True
    return "broken" in fail_on and bool(report.by_verdict(Verdict.BROKEN))


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flakehound",
        description="Find flaky tests and prove why they're flaky.",
    )
    parser.add_argument("--version", action="version", version=f"flakehound {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run",
        help="run the test suite many times under varied conditions",
        description=(
            "Runs your pytest suite several times with shuffled order and varied seeds, "
            "plus probe runs in other timezones and on risky dates, then reports and explains "
            "flaky tests and time bombs. WARNING: this executes your test code; "
            "only run it on code you trust. Extra pytest arguments go after '--'."
        ),
    )
    run.add_argument("paths", nargs="*", help="test paths passed to pytest (default: pytest's)")
    run.add_argument("-n", "--runs", type=_positive_int, default=10, help="number of runs")
    run.add_argument(
        "-j", "--workers", type=_positive_int, default=_default_workers(), help="parallel runs"
    )
    run.add_argument("--seed", type=int, default=None, help="master seed (to replay a session)")
    run.add_argument(
        "--timeout", type=float, default=600.0, help="seconds before a single run is killed"
    )
    run.add_argument("--json", type=Path, default=None, metavar="PATH", help="write JSON report")
    run.add_argument(
        "--python", default=None, help="python interpreter for tests (default: this one)"
    )
    run.add_argument(
        "--no-diagnose", action="store_true", help="only detect flaky tests, don't explain them"
    )
    run.add_argument(
        "--max-diagnose",
        type=_positive_int,
        default=20,
        help="diagnose at most this many flaky tests (default 20)",
    )
    run.add_argument(
        "--no-probes",
        action="store_true",
        help="skip all probe runs (timezones, risky dates, timing)",
    )
    run.add_argument(
        "--no-timing",
        action="store_true",
        help="skip the timing probes (shorter sleeps, thread-switch stress)",
    )
    run.add_argument(
        "--markdown",
        type=Path,
        default=None,
        metavar="PATH",
        help="write a Markdown report (for PR comments)",
    )
    run.add_argument(
        "--no-step-summary",
        action="store_true",
        help="don't add the Markdown report to the GitHub Actions job summary",
    )
    run.add_argument(
        "--fail-on",
        type=_fail_on,
        default=DEFAULT_FAIL_ON,
        metavar="KINDS",
        help=(
            "what makes the exit code 1, comma-separated from: "
            f"{', '.join(FAIL_ON_CHOICES)} (default: {','.join(DEFAULT_FAIL_ON)}; "
            "'none' never fails)"
        ),
    )
    cli_quarantine.add_parser(sub)
    return parser


def _split_passthrough(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    argv = list(argv)
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def _cmd_run(args: argparse.Namespace, extra: list[str], console: Console) -> int:
    procs = ProcessRegistry()
    seed = args.seed if args.seed is not None else secrets.randbelow(2**31)
    runner = PytestRunner(
        Path.cwd(),
        [*args.paths, *extra],
        python=args.python,
        procs=procs,
        timeout=args.timeout,
    )
    terminal.header(console, runner.name, args.runs, args.workers, seed)

    from flakehound.session import run_session

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    task = progress.add_task("collecting tests", total=None)

    def collected(n_tests: int, n_runs: int) -> None:
        progress.update(task, description=f"running {n_tests} tests", total=n_runs)

    def finished(_: RunResult) -> None:
        progress.advance(task)

    def diagnose_start(n: int) -> None:
        progress.update(task, description=f"diagnosing {n} tests", total=n, completed=0)

    def diagnosed(_: str) -> None:
        progress.advance(task)

    try:
        with progress:
            report = run_session(
                runner,
                procs,
                runs=args.runs,
                workers=args.workers,
                master_seed=seed,
                diagnose=not args.no_diagnose,
                max_diagnose=args.max_diagnose,
                probe=not args.no_probes,
                timing_probes=not args.no_timing,
                on_collected=collected,
                on_result=finished,
                on_diagnose_start=diagnose_start,
                on_diagnosed=diagnosed,
            )
    except CollectionError as exc:
        console.print(f"[bold red]error:[/] {exc}", highlight=False, markup=False)
        return EXIT_USAGE
    except KeyboardInterrupt:
        procs.kill_all()
        console.print("\n[yellow]interrupted, all test processes stopped[/]")
        return EXIT_INTERRUPTED

    if not report.test_ids:
        console.print("[yellow]no tests collected[/]")
        return EXIT_USAGE
    terminal.print_report(console, report)
    if args.json is not None:
        write_json(report, args.json)
        console.print(f"[dim]JSON report written to {args.json}[/]", highlight=False)
    if args.markdown is not None:
        write_markdown(report, args.markdown)
        console.print(f"[dim]Markdown report written to {args.markdown}[/]", highlight=False)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and not args.no_step_summary:
        # GitHub Actions shows this file on the run's summary page.
        write_markdown(report, Path(summary), append=True)
    return EXIT_FLAKY if should_fail(report, args.fail_on) else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    own, extra = _split_passthrough(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(own)
    # Never crash on terminals that can't print some characters (old Windows consoles).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    console = Console()
    if args.command == "run":
        return _cmd_run(args, extra, console)
    if args.command == "quarantine":
        return cli_quarantine.run(args, console)
    return EXIT_USAGE  # pragma: no cover - argparse enforces the subcommand
