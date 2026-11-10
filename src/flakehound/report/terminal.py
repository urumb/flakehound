"""Human-friendly terminal report (rich)."""

from __future__ import annotations

import shlex

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from flakehound import __version__
from flakehound.detectors.shake import TestStats, Verdict
from flakehound.diagnosis.env import EnvCause, EnvDiagnosis
from flakehound.diagnosis.order import OrderDiagnosis, OrderKind
from flakehound.session import Report

_STYLE = {
    Verdict.FLAKY: "bold black on yellow",
    Verdict.BROKEN: "bold white on red",
}
_BOMB_STYLE = "bold white on dark_orange3"
_ENV_CAUSE = {
    EnvCause.RANDOM_SEED: "RANDOMNESS",
    EnvCause.HASH_SEED: "SET/DICT ORDER (hash seed)",
    EnvCause.SEEDS_COMBINED: "RANDOMNESS + SET/DICT ORDER",
    EnvCause.NONDETERMINISTIC: "NONDETERMINISTIC (not order, not seeds)",
    EnvCause.TIMEZONE: "TIMEZONE",
    EnvCause.CLOCK: "DATE/CLOCK",
}
_MAX_RUN_IDS = 8


class _Sym:
    """Fancy symbols when the terminal can print them, ASCII otherwise."""

    def __init__(self, fancy: bool) -> None:
        self.dot = "·" if fancy else "|"
        self.dash = "–" if fancy else "-"  # en dash
        self.times = "×" if fancy else "x"  # multiplication sign
        self.ellipsis = "…" if fancy else "..."
        self.arrow = "→" if fancy else "->"


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


_MAX_STATE_LINES = 4
_PAD = "             "  # aligns continuation lines under the field values


def _field(console: Console, name: str, value: str) -> None:
    console.print(f"   [bold]{name + ':':<9}[/] {value}", highlight=False)


def _short(nodeid: str) -> str:
    return nodeid.split("::")[-1]


def _print_diagnosis(console: Console, d: OrderDiagnosis) -> None:
    sym = _sym(console)
    if not d.is_order:
        if d.kind is OrderKind.NOT_ORDER:
            _field(console, "Cause", f"[bold]not test order[/]: {escape(d.note)}")
        else:
            _field(console, "Cause", f"[dim]unknown: {escape(d.note)}[/]")
        return
    status = "confirmed" if d.confirmed else "suspected"
    culprits = ", ".join(escape(c) for c in d.culprits)
    if d.kind is OrderKind.POLLUTED:
        _field(console, "Cause", f"[bold magenta]ORDER DEPENDENCY[/] ({status})")
        role = "Polluter" if len(d.culprits) == 1 else "Polluters"
        _field(console, role, culprits)
    else:
        _field(
            console, "Cause", f"[bold magenta]ORDER DEPENDENCY: needs another test[/] ({status})"
        )
        _field(console, "Setter", culprits)
    if d.state_changes:
        label = "Leaked" if d.kind is OrderKind.POLLUTED else "Sets"
        for i, c in enumerate(d.state_changes[:_MAX_STATE_LINES]):
            prefix = "env " if c.kind == "env" else ""
            line = f"{prefix}{escape(c.name)}: {escape(c.before)} {sym.arrow} {escape(c.after)}"
            if c.kind == "file":
                line = f"file {escape(c.name)} {escape(c.after)}"
            if i == 0:
                _field(console, label, line)
            else:
                console.print(f"{_PAD}{line}", highlight=False)
        extra = len(d.state_changes) - _MAX_STATE_LINES
        if extra > 0:
            console.print(f"{_PAD}[dim]+{extra} more in the JSON report[/]", highlight=False)
    first = _short(d.culprits[0]) if len(d.culprits) == 1 else "the culprits"
    ev = d.evidence
    verb = "failed" if d.kind is OrderKind.POLLUTED else "passed"
    before = (
        ev.before_failed if d.kind is OrderKind.POLLUTED else ev.before_total - ev.before_failed
    )
    other = (
        ev.otherwise_failed
        if d.kind is OrderKind.POLLUTED
        else ev.otherwise_total - ev.otherwise_failed
    )
    _field(
        console,
        "Evidence",
        f"{verb} in {before}/{ev.before_total} runs where {escape(first)} ran first, "
        f"{other}/{ev.otherwise_total} otherwise",
    )
    if d.kind is OrderKind.POLLUTED:
        console.print(
            f"{_PAD}passes alone {d.alone_total - d.alone_failed}/{d.alone_total} with the "
            f"same seeds; fails {d.confirm_reproduced}/{d.confirm_total} right after "
            f"{escape(first)}",
            highlight=False,
        )
    else:
        console.print(
            f"{_PAD}fails alone {d.alone_pass_seeds_failed}/{d.alone_pass_seeds_total}; "
            f"passes {d.confirm_reproduced}/{d.confirm_total} right after {escape(first)}",
            highlight=False,
        )
    if d.fix:
        _field(console, "Fix", escape(d.fix))
    _print_repro(console, d.repro, {}, d.repro_verified)


def _print_repro(
    console: Console, repro: list[str], env: dict[str, str], verified: bool | None
) -> None:
    if not repro:
        return
    tag = ""
    if verified is True:
        tag = "[green](verified)[/]"
    elif verified is False:
        tag = "[yellow](did not reproduce outside flakehound)[/]"
    env_part = [f"{k}={v}" for k, v in env.items()]
    _field(console, "Repro", f"{escape(shlex.join([*env_part, *repro]))} {tag}")


def _print_env(console: Console, d: EnvDiagnosis) -> None:
    if d.cause is EnvCause.INCONCLUSIVE:
        _field(console, "Cause", f"[dim]unknown: {escape(d.note)}[/]")
        return
    status = "confirmed" if d.confirmed else "suspected"
    _field(console, "Cause", f"[bold magenta]{_ENV_CAUSE[d.cause]}[/] ({status})")
    if d.condition and not d.latent:
        _field(console, "Fails", f"with {escape(d.condition)}")
    for i, line in enumerate(d.evidence):
        if i == 0:
            _field(console, "Evidence", escape(line))
        else:
            console.print(f"{_PAD}{escape(line)}", highlight=False)
    if d.note:
        _field(console, "Note", f"[yellow]{escape(d.note)}[/]")
    if d.fix:
        _field(console, "Fix", escape(d.fix))
    _print_repro(console, d.repro, d.repro_env, d.repro_verified)


def _print_time_bomb(console: Console, d: EnvDiagnosis) -> None:
    console.print(Text(" TIME BOMB ", style=_BOMB_STYLE), Text(d.nodeid, style="bold"))
    console.print(f"   passes today, but fails [bold]{escape(d.condition)}[/]", highlight=False)
    _print_env(console, d)
    console.print()


def _print_test(
    console: Console,
    s: TestStats,
    diag: OrderDiagnosis | None = None,
    env: EnvDiagnosis | None = None,
) -> None:
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
        order_proven = diag is not None and diag.is_order and diag.confirmed
        if not order_proven and env is not None and env.cause is not EnvCause.INCONCLUSIVE:
            _print_env(console, env)
        elif diag is not None:
            _print_diagnosis(console, diag)
    else:
        console.print(
            f"   failed in every run ({s.failures}/{s.runs}): looks like a real bug, not "
            f"flakiness [dim](if it is flaky, it passes under {_pct(s.max_pass_rate)} of the "
            "time)[/]",
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
        _print_test(console, s, report.diagnoses.get(s.nodeid), report.env_diagnoses.get(s.nodeid))
    for bomb in report.time_bombs:
        _print_time_bomb(console, bomb)
    if report.not_diagnosed:
        console.print(
            f"[dim]{report.not_diagnosed} more flaky tests were not diagnosed "
            "(raise --max-diagnose to include them)[/]\n",
            highlight=False,
        )

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
    if report.probes:
        bombs = len(report.time_bombs)
        parts.insert(2, f"[bold dark_orange3]{bombs} time bombs[/]" if bombs else "0 time bombs")
    skipped = len(report.by_verdict(Verdict.SKIPPED))
    if skipped:
        parts.append(f"{skipped} skipped")
    console.print(
        f" {sym.dot} ".join(parts)
        + f"  [dim]({len(report.test_ids)} tests {sym.times} {report.runs} runs"
        + (f" + {len(report.probes)} probe runs" if report.probes else "")
        + (f" + {report.diagnosis_runs} diagnosis runs" if report.diagnosis_runs else "")
        + f" in {report.duration:.1f}s)[/]",
        highlight=False,
    )
    console.print(
        f"[dim]Replay this exact session: flakehound run --seed {report.master_seed} "
        f"-n {report.runs}[/]",
        highlight=False,
    )
