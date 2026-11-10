# flakehound

[![CI](https://github.com/urumb/flakehound/actions/workflows/ci.yml/badge.svg)](https://github.com/urumb/flakehound/actions/workflows/ci.yml)
[![CodeQL](https://github.com/urumb/flakehound/actions/workflows/codeql.yml/badge.svg)](https://github.com/urumb/flakehound/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Find flaky tests and prove *why* they're flaky.**

> 🚧 Early development (milestone 1 of 6). Not on PyPI yet.

Flaky tests pass sometimes and fail sometimes with no code change. Most tools just
retry them, which hides the problem. flakehound runs your pytest suite under
deliberately hostile conditions, finds the tests that flip between pass and fail,
and (as the diagnosis milestones land) explains the cause with evidence from real runs.

- 🆓 **Free forever.** No accounts, no servers, no paid services.
- 🔒 **Local and private.** Offline, no telemetry, secrets redacted from every report.
- 🧪 **Evidence, not guesses.** Every verdict comes with the runs that prove it,
  and every session can be replayed exactly with its seed.

## Quickstart

```bash
pip install "git+https://github.com/urumb/flakehound"
cd your-project
flakehound run
```

Real output on the [demo project](demo/):

```
flakehound 0.1.0.dev0 · pytest · 12 runs · 8 workers · seed 7

 FLAKY  tests/test_tags.py::test_tags_render_alphabetically
   failed 8/12 runs (67%, 95% CI 39%–86%)
   error (8×): AssertionError: assert 'python,flaky,pytest' == 'flaky,pytest,python'  (tests/test_tags.py)
   failing runs: #0, #2, #3, #7, #8, #9, #10, #11

 FLAKY  tests/test_cart.py::test_cart_starts_empty
   failed 7/12 runs (58%, 95% CI 32%–81%)
   error (7×): AssertionError: assert ['apple'] == []  (tests/test_cart.py)
   failing runs: #1, #2, #3, #5, #9, #10, #11

 FLAKY  tests/test_discount.py::test_lucky_discount_is_small
   failed 2/12 runs (17%, 95% CI 5%–45%)
   error (2×): assert 0.8342156493018773 < 0.8  (tests/test_discount.py)
   failing runs: #2, #7

3 flaky · 0 broken · 4 stable  (7 tests × 12 runs in 2.0s)
Replay this exact session: flakehound run --seed 7 -n 12
```

### Options

| Option | Meaning |
|---|---|
| `-n, --runs N` | How many times to run the suite (default 10) |
| `-j, --workers N` | Parallel runs (default: CPU cores − 1, max 8) |
| `--seed N` | Replay an earlier session exactly |
| `--json PATH` | Also write a machine-readable report |
| `--timeout S` | Kill a single run after S seconds (default 600) |
| `-- ARGS` | Anything after `--` goes straight to pytest |

Exit codes: `0` no flaky tests, `1` flaky tests found, `2` usage or collection error.

## How it works

1. **Shake.** Run the whole suite many times in parallel. Each run uses a different
   test order, `random` seed and `PYTHONHASHSEED`, all recorded.
2. **Classify.** Tests that both pass and fail are *flaky*; tests that always fail are
   *broken* (a real bug, not flakiness). Failure rates come with 95% confidence intervals.
3. **Diagnose** *(coming next)*. One factor at a time: find the exact polluter test and
   the leaked state for order dependencies, and test seeds, timezones, dates and timing.
4. **Verify** *(coming)*. Each diagnosis gets a repro command that must fail every
   time, and a counterfactual that must pass.

## Roadmap

- [x] **M1** Core engine, parallel shuffled runs, terminal + JSON reports, demo
- [ ] **M2** Order dependency: isolation, bisection to the polluter, *which state leaked*
- [ ] **M3** Environment: random seed, hash seed, timezone, "danger day" dates
- [ ] **M4** Timing: sleep shrinking, thread-switch stress, CPU load
- [ ] **M5** Verified repro commands, Markdown/PR output, CI mode, quarantine
- [ ] **M6** First PyPI release
- Later: GitHub Actions history mode, Jest/Go/JUnit adapters, optional local-AI explanations

## Security

flakehound **runs your test code**. Only run it on code you trust. It makes no network
calls, collects no telemetry and never writes environment variables into reports.
See [SECURITY.md](SECURITY.md) to report a vulnerability.

## License

MIT
