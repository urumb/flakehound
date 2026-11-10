# Contributing to flakehound

Thanks for helping! Bug reports with a small project that reproduces the problem are
the most valuable contribution of all.

## Ground rules

- **Free and private.** No paid services, no telemetry, no network calls unless a user
  explicitly turns a feature on.
- **Safe.** Never `shell=True` or string-built commands; always clean up child processes
  and temp files; never print or store secrets.
- **Evidence, not guesses.** A diagnosis must be backed by runs that prove it. If
  something can't be proven, say "suspected".
- **Small dependencies.** Only add one if it's permissively licensed (MIT/BSD/Apache-2.0)
  and clearly worth it. Discuss it in an issue first.

## Development setup

```bash
git clone https://github.com/urumb/flakehound
cd flakehound
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
python -m pip install --require-hashes -r requirements/dev.txt
python -m pip install --no-deps --no-build-isolation -e .
```

## Checks (CI runs the same)

```bash
ruff check .
ruff format --check .
mypy
python -m pytest
```

Most tests run real pytest subprocesses on small generated projects, so the suite takes
a few minutes. Try the tool on the demo:

```bash
cd demo
flakehound run
```

## Changing dependencies

Dev tools are locked with hashes. After editing `requirements/dev.in`:

```bash
uv pip compile --universal --generate-hashes --python-version 3.10 requirements/dev.in -o requirements/dev.txt
```

## Architecture in one minute

- `core/`: runner-agnostic models, process handling, scheduling, stats.
- `adapters/pytest/`: everything pytest-specific (worker plugin, snapshots, clock, timing).
  Other test runners would be new adapters.
- `detectors/`: run planning (shake runs, probes).
- `diagnosis/`: order, environment and timing diagnosis, fix suggestions.
- `report/`: terminal, JSON, Markdown, redaction.

## Security issues

Please don't open a public issue; see [SECURITY.md](SECURITY.md).
