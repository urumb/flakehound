"""Regenerate the README visuals from a real run on the demo project.

    python scripts/make_demo_assets.py

Writes docs/demo.svg (the terminal report, rendered by rich) and
docs/example-report.md (the Markdown report). No screen recorder needed.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI

from flakehound.adapters.pytest.runner import PytestRunner
from flakehound.core.procs import ProcessRegistry
from flakehound.report import terminal
from flakehound.report.markdown import render
from flakehound.session import run_session

ROOT = Path(__file__).resolve().parents[1]
SEED, RUNS, WORKERS = 7, 12, 4


def main() -> int:
    procs = ProcessRegistry()
    runner = PytestRunner(ROOT / "demo", procs=procs, timeout=300)
    report = run_session(runner, procs, runs=RUNS, workers=WORKERS, master_seed=SEED)
    # Render into a buffer (UTF-8), not the real terminal: fancy symbols, no noise.
    console = Console(
        file=io.StringIO(), record=True, width=104, force_terminal=True, color_system="truecolor"
    )
    console.print("[dim]$[/] flakehound run", highlight=False)
    terminal.header(console, runner.name, RUNS, WORKERS, SEED)
    terminal.print_report(console, report)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    console.save_svg(str(docs / "demo.svg"), title="flakehound run", theme=MONOKAI)
    (docs / "example-report.md").write_text(render(report), encoding="utf-8")
    print(f"wrote {docs / 'demo.svg'} and {docs / 'example-report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
