# Changelog

All notable changes to flakehound are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break things).

## [Unreleased]

### Added
- GitHub Action (`uses: urumb/flakehound@...`): installs flakehound from the action's own
  source, runs it with your options, adds the Markdown report to the job summary, and
  exposes `exit-code` and `report` outputs. Inputs reach the shell only via environment
  variables (no script injection).
- Issue forms (including "flakehound got it wrong"), a pull request template, Discussions.

## [0.1.0] - 2026-11-10

First release. Python + pytest.

### Detection
- `flakehound run`: runs the suite many times in parallel with shuffled order and varied
  `random` seed and `PYTHONHASHSEED`; failure rates with 95% confidence intervals.
- Tests that fail every run are rechecked alone before being called broken.
- Probe runs find **time bombs** (UTC+14, UTC−12, just before midnight, last day of the
  month, New Year's Eve, Feb 29) and **racy tests** (main-thread sleeps 20× shorter;
  thread-switch stress).

### Diagnosis (every verdict backed by real runs)
- Order dependency: isolation with the failing runs' exact seeds, polluter or
  state-setter search (correlation fast path + parallel delta debugging), and the
  **leaked state** named (module globals, object/class attributes, env vars, cwd,
  `sys.path`, project files, logging).
- Randomness vs set/dict ordering (hash seed), told apart by swapping one seed at a time.
- Timezone and date/clock time bombs.
- Sleep used as a wait, pinned to the exact `sleep()` line; race conditions.
- Rule-based fix suggestions; repro commands that are run for real and marked *verified*.

### Output and CI
- Terminal report, JSON report (schema v3), Markdown report (`--markdown`), and the
  GitHub Actions job summary.
- `--fail-on` to choose what fails a build.
- Quarantine: `.flakehound/quarantine.toml` + pytest plugin (non-strict xfail or skip)
  + `flakehound quarantine list|add|remove` with diff and confirmation.
- Works with projects that use pytest-xdist (`-n auto` in addopts).

### Security and cost
- Fully offline, no telemetry, secrets redacted from every report.
- No shell commands; every child process tree is cleaned up on timeout or Ctrl+C.
- Runtime dependencies: pytest, rich, and tomli on Python 3.10 only.

### Known limitations
- `asyncio.sleep` is not shortened by the timing probes.
- State hidden in fixtures, databases or external services is not visible to the leak
  detector (the report says so instead of guessing).
- Thread stress needs the race to happen around a Python function call.

[Unreleased]: https://github.com/urumb/flakehound/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/urumb/flakehound/releases/tag/v0.1.0
