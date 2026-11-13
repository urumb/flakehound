"""Markdown report: for pull request comments and the GitHub Actions job summary.

GitHub-flavoured Markdown: a summary line, one table per kind of problem, and a
collapsible ``<details>`` block per test with evidence, fix and repro command.
All text comes from already-redacted findings and is escaped here.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from flakehound import __version__
from flakehound.detectors.shake import Verdict
from flakehound.report.findings import Finding, build_findings
from flakehound.session import Report

_SECTIONS = [
    ("flaky", "Flaky tests", "Pass sometimes and fail sometimes with no code change."),
    ("time_bomb", "Time bombs", "Pass today, but fail on certain dates or in other timezones."),
    (
        "racy",
        "Racy tests",
        "Pass normally, but fail on a slower machine or when threads interleave.",
    ),
    ("broken", "Broken tests", "Failed in every run: probably real bugs, not flakiness."),
]


def code(text: str) -> str:
    """Inline code that survives backticks inside ``text``."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def cell(text: str) -> str:
    """Make text safe inside a table cell (no pipes or line breaks)."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _inline(text: str) -> str:
    """Plain text that may contain `code` spans: escape HTML, keep the backticks."""
    return cell(html.escape(text, quote=False))


def _fence(text: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * (longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def _details(f: Finding) -> list[str]:
    status = f" ({f.status})" if f.status else ""
    out = [
        "<details>",
        f"<summary><code>{html.escape(f.nodeid)}</code>: {html.escape(f.cause)}{status}</summary>",
        "",
        f"- **Result:** {_inline(f.summary)}",
    ]
    for label, lines in f.fields:
        if label in ("Polluter", "Setter"):
            out.append(f"- **{label}:** " + ", ".join(code(x) for x in lines))
        elif label in ("Leaked", "Sets"):
            out.append(f"- **{label}:** " + "<br>".join(code(x) for x in lines))
        elif len(lines) == 1:
            out.append(f"- **{label}:** {_inline(lines[0])}")
        else:
            out.append(f"- **{label}:**")
            out += [f"  - {_inline(x)}" for x in lines]
    if f.fix:
        out.append(f"- **Fix:** {_inline(f.fix)}")
    for text, count in f.errors:
        out.append(f"- **Error ({count}×):** {code(text)}")
    if f.repro:
        verified = ""
        if f.repro_verified is True:
            verified = " (verified)"
        elif f.repro_verified is False:
            verified = " (did not reproduce outside flakehound)"
        out += ["", f"Reproduce{verified}:", "", _fence(f.repro, "bash")]
    out += ["", "</details>", ""]
    return out


def render(report: Report) -> str:
    findings = build_findings(report)
    by_kind = {kind: [f for f in findings if f.kind == kind] for kind, _, _ in _SECTIONS}
    stable = len(report.by_verdict(Verdict.STABLE))
    counts = [f"{len(by_kind[k])} {title.lower()}" for k, title, _ in _SECTIONS if by_kind[k]]
    headline = ", ".join(counts) if counts else "no problems found"
    runs = f"{len(report.test_ids)} tests × {report.runs} runs"
    if report.probes:
        runs += f" + {len(report.probes)} probe runs"
    if report.diagnosis_runs:
        runs += f" + {report.diagnosis_runs} diagnosis runs"

    out = [
        "## flakehound report",
        "",
        f"**{headline.capitalize()}** ({stable} stable). {runs} in {report.duration:.1f}s.",
        "",
    ]
    for kind, title, blurb in _SECTIONS:
        items = by_kind[kind]
        if not items:
            continue
        out += [f"### {title}", "", blurb, "", "| Test | Result | Cause |", "|---|---|---|"]
        for f in items:
            out.append(f"| {code(f.nodeid)} | {_inline(f.summary)} | {_inline(f.short_cause)} |")
        out.append("")
        for f in items:
            out += _details(f)
    if report.not_diagnosed:
        out += [f"_{report.not_diagnosed} more flaky tests were not diagnosed._", ""]
    crashed = report.crashed_runs
    if crashed:
        out += [f"_{len(crashed)} run(s) did not finish; see the JSON report._", ""]
        if report.parallel_timeout_hint:
            out += [f"_{report.parallel_timeout_hint}_", ""]
    out += [
        f"<sub>flakehound {__version__} · replay this session: "
        f"{code(f'flakehound run --seed {report.master_seed} -n {report.runs}')}</sub>",
        "",
    ]
    return "\n".join(out)


def write_markdown(report: Report, path: Path, *, append: bool = False) -> None:
    text = render(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
