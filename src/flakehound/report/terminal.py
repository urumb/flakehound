"""Human-friendly terminal report (rich)."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from flakehound import __version__
from flakehound.detectors.shake import TestStats, Verdict
from flakehound.session import Report

_STYLE = {
    Verdict.FLAKY: "bold black on yellow",
    Verdict.BROKEN: "bold white on red",
}
_MAX_RUN_IDS = 8


class _Sym:
    """Fancy symbols when the terminal can print them, ASCII otherwise."""

    def __init__(self, fancy: bool) -> None:
        self.dot = "·" if fancy else "|"
        self.dash = "–" if fancy else "-"  # en dash
        self.times = "×" if fancy else "x"  # multiplication sign
        self.ellipsis = "…" if fancy else "..."


def _sym(console: Console) -> _Sym:
    encoding = (console.encoding or "").lower().replace("-", "").replace("_", "")
    return _Sym(encoding in ("utf8", "utf16", "utf32"))


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def header(console: Console, runner: str, runs: int, workers: int, seed: int) -> None:
    d = _sym(console).dot
    console.print(
        f"[bold]flakehound[/] {__version__} {d} {runner} {d} {runs} runs {d} "
        f"{workers} workers {d} seed {seed}",
        highlight=False,
    )


def _print_test(console: Console, s: TestStats) -> None:
    sym = _sym(console)
    label = Text(f" {s.verdict.value.upper()} ", style=_STYLE[s.verdict])
    console.print(label, Text(s.nodeid, style="bold"))
    if s.verdict is Verdict.FLAKY:
        lo, hi = s.interval
        console.print(
            f"   failed [bold]{s.failures}/{s.runs}[/] runs "
            f"({_pct(s.failure_rate)}, 95% CI {_pct(lo)}{sym.dash}{_pct(hi)})",
            highlight=False,
        )
    else:
        console.print(
            f"   failed in every run ({s.failures}/{s.runs}): a real failure, not flakiness",
            highlight=False,
        )
    for text, count in s.top_messages(2):
        console.print(f"   [dim]error ({count}{sym.times}):[/] {escape(text)}", highlight=False)
    if s.verdict is Verdict.FLAKY:
        ids = ", ".join(f"#{i}" for i in s.failing_runs[:_MAX_RUN_IDS])
        more = f" {sym.ellipsis}" if len(s.failing_runs) > _MAX_RUN_IDS else ""
        console.print(f"   [dim]failing runs:[/] {ids}{more}", highlight=False)
    console.print()


def print_report(console: Console, report: Report) -> None:
    console.print()
    flaky = report.by_verdict(Verdict.FLAKY)
    broken = report.by_verdict(Verdict.BROKEN)
    for s in flaky + broken:
        _print_test(console, s)

    for r in report.crashed_runs:
        console.print(
            f"[bold red]run #{r.spec.run_id} did not finish:[/] {escape(r.error)}",
            highlight=False,
        )
    if report.crashed_runs:
        console.print()

    sym = _sym(console)
    parts = [
        f"[bold yellow]{len(flaky)} flaky[/]" if flaky else "[green]0 flaky[/]",
        f"[bold red]{len(broken)} broken[/]" if broken else "0 broken",
        f"{len(report.by_verdict(Verdict.STABLE))} stable",
    ]
    skipped = len(report.by_verdict(Verdict.SKIPPED))
    if skipped:
        parts.append(f"{skipped} skipped")
    console.print(
        f" {sym.dot} ".join(parts)
        + f"  [dim]({len(report.test_ids)} tests {sym.times} {report.runs} runs in "
        f"{report.duration:.1f}s)[/]",
        highlight=False,
    )
    console.print(
        f"[dim]Replay this exact session: flakehound run --seed {report.master_seed} "
        f"-n {report.runs}[/]",
        highlight=False,
    )
