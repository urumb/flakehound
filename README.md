# flakehound

[![CI](https://github.com/urumb/flakehound/actions/workflows/ci.yml/badge.svg)](https://github.com/urumb/flakehound/actions/workflows/ci.yml)
[![CodeQL](https://github.com/urumb/flakehound/actions/workflows/codeql.yml/badge.svg)](https://github.com/urumb/flakehound/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Find flaky tests and prove *why* they're flaky.**

> 🚧 Early development (milestone 2 of 6). Not on PyPI yet.

Flaky tests pass sometimes and fail sometimes with no code change. Most tools just
retry them, which hides the problem. flakehound runs your pytest suite under
deliberately hostile conditions, finds the tests that flip between pass and fail,
and explains the cause with evidence from real runs.

- 🆓 **Free forever.** No accounts, no servers, no paid services.
- 🔒 **Local and private.** Offline, no telemetry, secrets redacted from every report.
- 🧪 **Evidence, not guesses.** Every verdict comes with the runs that prove it,
  and every repro command is verified by actually running it.

## Quickstart

```bash
pip install "git+https://github.com/urumb/flakehound"
cd your-project
flakehound run
```

Real output on the [demo project](demo/) (trimmed):

```
 FLAKY  tests/test_cart.py::test_cart_starts_empty
   failed 7/16 runs (44%, 95% CI 23%–67%)
   Cause:    ORDER DEPENDENCY (confirmed)
   Polluter: tests/test_cart.py::test_add_item
   Leaked:   shop.cart: [] → ['apple']
   Evidence: failed in 7/7 runs where test_add_item ran first, 0/9 otherwise
             passes alone 3/3 with the same seeds; fails 3/3 right after test_add_item
   Fix:      in test_add_item: reset `shop.cart` around each test, e.g. an autouse
             fixture that clears it after the test (`yield` then `shop.cart.clear()`)
   Repro:    pytest -p no:randomly -p no:cacheprovider tests/test_cart.py::test_add_item
             tests/test_cart.py::test_cart_starts_empty (verified)

 FLAKY  tests/test_currency.py::test_prices_default_to_usd
   Cause:    ORDER DEPENDENCY (confirmed)
   Polluter: tests/test_currency.py::test_euro_store
   Leaked:   env SHOP_CURRENCY: <missing> → 'EUR'
   Fix:      in test_euro_store: use `monkeypatch.setenv`/`monkeypatch.delenv`
             so `SHOP_CURRENCY` is restored

 FLAKY  tests/test_inventory.py::test_apples_in_stock
   Cause:    ORDER DEPENDENCY: needs another test (confirmed)
   Setter:   tests/test_inventory.py::test_restock_apples
   Sets:     shop.Inventory.stock: {} → {'apple': 5}

 FLAKY  tests/test_tags.py::test_tags_render_alphabetically
   Cause:    not test order: fails on its own (3/3 runs with the seeds of the
             failing runs), so test order is not the cause

5 flaky · 0 broken · 6 stable  (11 tests × 16 runs + 46 diagnosis runs in 8.7s)
```

### Options

| Option | Meaning |
|---|---|
| `-n, --runs N` | How many times to run the suite (default 10) |
| `-j, --workers N` | Parallel runs (default: CPU cores − 1, max 8) |
| `--seed N` | Replay an earlier session exactly |
| `--json PATH` | Also write a machine-readable report |
| `--timeout S` | Kill a single run after S seconds (default 600) |
| `--no-diagnose` | Only detect flaky tests, don't explain them |
| `--max-diagnose N` | Diagnose at most N flaky tests (default 20) |
| `-- ARGS` | Anything after `--` goes straight to pytest |

Exit codes: `0` no flaky tests, `1` flaky tests found, `2` usage or collection error.

## How it works

flakehound changes **one factor at a time**, like a science experiment:

1. **Shake.** Run the whole suite many times in parallel. Each run uses a different
   test order, `random` seed and `PYTHONHASHSEED`, all recorded. Tests that both pass
   and fail are *flaky*; tests that always fail are *broken* (a real bug).
2. **Isolate.** Run each flaky test alone with the *exact seeds* of the runs where it
   failed. Only the order changed, so if it now passes, order is proven to be the cause.
3. **Find the culprit.** Try the tests whose position best correlates with the failure,
   then shrink the list of preceding tests with delta debugging, in parallel batches.
   Works for single polluters, combinations, and tests that *need* another test first.
4. **Name the leaked state.** Snapshot global state before and after the culprit: module
   variables, class and object attributes, environment variables (secret values are never
   shown), working directory, `sys.path`, project files and logging config.
5. **Confirm and verify.** Re-run culprit + victim, then run the printed plain-pytest
   repro command for real and mark it *verified* only if it fails outside flakehound.

## Roadmap

- [x] **M1** Core engine, parallel shuffled runs, terminal + JSON reports, demo
- [x] **M2** Order dependency: isolation, polluter/setter search, *which state leaked*, verified repros
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
