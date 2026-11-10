"""Turn a :class:`Report` into plain, renderer-neutral findings.

Each finding is one problem test with a short cause and labelled lines of
evidence, all plain text (already redacted upstream). The Markdown report
renders these; future renderers (a GitHub Action comment, HTML) can too.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from flakehound.detectors.shake import TestStats, Verdict
from flakehound.diagnosis.env import EnvCause, EnvDiagnosis
from flakehound.diagnosis.order import OrderDiagnosis, OrderKind
from flakehound.session import Report

ENV_CAUSE_LABEL = {
    EnvCause.RANDOM_SEED: "Randomness",
    EnvCause.HASH_SEED: "Set/dict order (hash seed)",
    EnvCause.SEEDS_COMBINED: "Randomness + set/dict order",
    EnvCause.NONDETERMINISTIC: "Nondeterministic (not order, not seeds)",
    EnvCause.TIMEZONE: "Timezone",
    EnvCause.CLOCK: "Date/clock",
    EnvCause.TIMING: "Timing (sleep used as a wait)",
    EnvCause.RACE: "Race condition",
}
TIMING_CAUSES = (EnvCause.TIMING, EnvCause.RACE)
MAX_STATE_LINES = 4


@dataclass
class Finding:
    nodeid: str
    kind: str  # "flaky", "broken", "time_bomb", "racy"
    summary: str  # e.g. "failed 7/12 runs (58%)" or "fails on the last day of the month"
    cause: str = ""  # e.g. "Order dependency"
    status: str = ""  # "confirmed", "suspected" or ""
    short_cause: str = ""  # one line for tables
    fields: list[tuple[str, list[str]]] = field(default_factory=list)
    fix: str = ""
    repro: str = ""
    repro_verified: bool | None = None
    errors: list[tuple[str, int]] = field(default_factory=list)


def _short(nodeid: str) -> str:
    return nodeid.split("::")[-1]


def repro_text(cmd: list[str], env: dict[str, str]) -> str:
    return shlex.join([*(f"{k}={v}" for k, v in env.items()), *cmd]) if cmd else ""


def _order_finding(f: Finding, d: OrderDiagnosis) -> None:
    if not d.is_order:
        if d.kind is OrderKind.NOT_ORDER:
            f.cause, f.short_cause = "Not test order", f"Not test order: {d.note}"
        else:
            f.cause, f.short_cause = "Unknown", f"Unknown: {d.note}"
        f.fields.append(("Note", [d.note]))
        return
    f.status = "confirmed" if d.confirmed else "suspected"
    culprits = [_short(c) for c in d.culprits]
    polluted = d.kind is OrderKind.POLLUTED
    f.cause = "Order dependency" if polluted else "Order dependency: needs another test"
    who = ", ".join(f"`{c}`" for c in culprits)
    f.short_cause = (
        f"{f.cause} ({f.status}): polluted by {who}"
        if polluted
        else f"Order dependency ({f.status}): only passes after {who}"
    )
    f.fields.append(("Polluter" if polluted else "Setter", list(d.culprits)))
    if d.state_changes:
        lines = []
        for c in d.state_changes[:MAX_STATE_LINES]:
            if c.kind == "file":
                lines.append(f"file {c.name} {c.after}")
            else:
                prefix = "env " if c.kind == "env" else ""
                lines.append(f"{prefix}{c.name}: {c.before} → {c.after}")
        extra = len(d.state_changes) - MAX_STATE_LINES
        if extra > 0:
            lines.append(f"+{extra} more in the JSON report")
        f.fields.append(("Leaked" if polluted else "Sets", lines))
    ev = d.evidence
    first = culprits[0] if len(culprits) == 1 else "the culprits"
    if polluted:
        f.fields.append(
            (
                "Evidence",
                [
                    f"failed in {ev.before_failed}/{ev.before_total} runs where {first} ran "
                    f"first, {ev.otherwise_failed}/{ev.otherwise_total} otherwise",
                    f"passes alone {d.alone_total - d.alone_failed}/{d.alone_total} with the "
                    f"same seeds; fails {d.confirm_reproduced}/{d.confirm_total} right after "
                    f"{first}",
                ],
            )
        )
    else:
        f.fields.append(
            (
                "Evidence",
                [
                    f"passed in {ev.before_total - ev.before_failed}/{ev.before_total} runs "
                    f"where {first} ran first, "
                    f"{ev.otherwise_total - ev.otherwise_failed}/{ev.otherwise_total} otherwise",
                    f"fails alone {d.alone_pass_seeds_failed}/{d.alone_pass_seeds_total}; "
                    f"passes {d.confirm_reproduced}/{d.confirm_total} right after {first}",
                ],
            )
        )
    f.fix = d.fix
    f.repro = repro_text(d.repro, {})
    f.repro_verified = d.repro_verified


def _env_finding(f: Finding, d: EnvDiagnosis) -> None:
    f.status = "confirmed" if d.confirmed else "suspected"
    f.cause = ENV_CAUSE_LABEL.get(d.cause, d.cause.value)
    f.short_cause = f"{f.cause} ({f.status})"
    if d.condition and not d.latent:
        f.short_cause += (
            f": fails {d.condition}"
            if d.cause in TIMING_CAUSES
            else (f": fails with {d.condition}")
        )
    if d.evidence:
        f.fields.append(("Evidence", list(d.evidence)))
    if d.note:
        f.fields.append(("Note", [d.note]))
    f.fix = d.fix
    f.repro = repro_text(d.repro, d.repro_env)
    f.repro_verified = d.repro_verified


def _flaky(s: TestStats, report: Report) -> Finding:
    f = Finding(
        s.nodeid,
        "flaky",
        f"failed {s.failures}/{s.runs} runs ({s.failure_rate * 100:.0f}%)",
        errors=s.top_messages(2),
    )
    order = report.diagnoses.get(s.nodeid)
    env = report.env_diagnoses.get(s.nodeid)
    order_proven = order is not None and order.is_order and order.confirmed
    if not order_proven and env is not None and env.cause is not EnvCause.INCONCLUSIVE:
        _env_finding(f, env)
    elif order is not None:
        _order_finding(f, order)
    else:
        f.cause = f.short_cause = "Not diagnosed"
    return f


def _broken(s: TestStats) -> Finding:
    return Finding(
        s.nodeid,
        "broken",
        f"failed in every run ({s.failures}/{s.runs}); if it is flaky it passes under "
        f"{s.max_pass_rate * 100:.0f}% of the time",
        cause="Real failure (not flaky)",
        short_cause="Fails every run: probably a real bug",
        errors=s.top_messages(2),
    )


def _latent(d: EnvDiagnosis) -> Finding:
    racy = d.cause in TIMING_CAUSES
    when = "passes normally" if racy else "passes today"
    f = Finding(d.nodeid, "racy" if racy else "time_bomb", f"{when}, but fails {d.condition}")
    _env_finding(f, d)
    return f


def build_findings(report: Report) -> list[Finding]:
    findings = [_flaky(s, report) for s in report.by_verdict(Verdict.FLAKY)]
    findings += [_latent(d) for d in report.latent]
    findings += [_broken(s) for s in report.by_verdict(Verdict.BROKEN)]
    return findings
