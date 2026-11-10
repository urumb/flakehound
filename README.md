# flakehound

[![CI](https://github.com/urumb/flakehound/actions/workflows/ci.yml/badge.svg)](https://github.com/urumb/flakehound/actions/workflows/ci.yml)
[![CodeQL](https://github.com/urumb/flakehound/actions/workflows/codeql.yml/badge.svg)](https://github.com/urumb/flakehound/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Find flaky tests and prove *why* they're flaky.**

> 🚧 Early development (milestone 5 of 6). Not on PyPI yet.

Flaky tests pass sometimes and fail sometimes with no code change. Most tools just
retry them, which hides the problem. flakehound runs your pytest suite under
deliberately hostile conditions, finds the tests that flip between pass and fail,
and explains the cause with evidence from real runs. It also finds problems that
haven't bitten you *yet*: **time bombs** (tests that will fail on the last day of the
month, on Feb 29, or on a CI machine in another timezone) and **racy tests** (a
`sleep()` used as a wait that breaks on a slow machine, or a race between threads).

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
   Cause:    SET/DICT ORDER (hash seed) (confirmed)
   Evidence: alone, with a failing run's seeds: failed 4/4; with a passing run's seeds: passed 2/2
             changing only PYTHONHASHSEED flips the result; changing only the random seed does not
   Fix:      sort before comparing (e.g. `sorted(...)`) or compare as sets
   Repro:    PYTHONHASHSEED=2522798642 pytest -p no:randomly -p no:cacheprovider
             tests/test_tags.py::test_tags_render_alphabetically (verified)

 TIME BOMB  tests/test_dates.py::test_coupon_valid_until_tomorrow
   passes today, but fails on the last day of the month, on New Year's Eve, two seconds
   before midnight and on a leap day (Feb 29)
   Cause:    DATE/CLOCK (confirmed)
   Fix:      date arithmetic breaks on the last day of the month: use `timedelta` ...
   Repro:    pytest ... --flakehound-clock=2026-11-30T12:00:00
             tests/test_dates.py::test_coupon_valid_until_tomorrow (verified)

 TIME BOMB  tests/test_dates.py::test_order_date_matches_utc
   passes today, but fails in timezone UTC-12
   Cause:    TIMEZONE (confirmed)
   Repro:    TZ=FHT+12 pytest -p no:randomly -p no:cacheprovider
             tests/test_dates.py::test_order_date_matches_utc (verified)

 RACY  tests/test_upload.py::test_upload_finishes
   passes normally, but fails when the `sleep(0.06)` at tests/test_upload.py:9 is shorter
   (a slower machine)
   Cause:    TIMING (sleep used as a wait) (confirmed)
   Fix:      `sleep(0.06)` at tests/test_upload.py:9 is used to wait for work that isn't
             guaranteed to be finished; wait for the actual event instead ...
   Repro:    pytest ... --flakehound-sleep-scale=0.1
             --flakehound-sleep-sites=tests/test_upload.py:9 tests/test_upload.py::test_upload_finishes (verified)

 RACY  tests/test_wallet.py::test_concurrent_top_ups
   passes normally, but fails when threads switch more often (a race condition)
   Cause:    RACE CONDITION (confirmed)
   Evidence: alone, with frequent thread switches: failed 4/4; normal: failed 0/2

5 flaky · 0 broken · 3 time bombs · 2 racy · 10 stable  (15 tests × 12 runs + 8 probe runs + ...)
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
| `--no-probes` | Skip all probe runs (timezones, risky dates, timing) |
| `--no-timing` | Skip only the timing probes (shorter sleeps, thread stress) |
| `--markdown PATH` | Also write a Markdown report (for PR comments) |
| `--no-step-summary` | Don't add the report to the GitHub Actions job summary |
| `--fail-on KINDS` | What makes exit code 1: `flaky`, `time-bomb`, `racy`, `broken`, `suspected`, `none` (default `flaky,time-bomb,racy`) |
| `-- ARGS` | Anything after `--` goes straight to pytest |

Exit codes: `0` no problems, `1` problems found (see `--fail-on`; by default only flaky tests and
*confirmed* time bombs or racy tests fail a build), `2` usage or collection error.

Repro commands that replay a seed, clock or timing stress use flakehound's pytest plugin
(`--flakehound-seed`, `--flakehound-clock`, `--flakehound-sleep-scale`,
`--flakehound-switch-interval`), so they need flakehound installed. It never
loads into your normal pytest runs unless you pass `-p flakehound.adapters.pytest.plugin`.

## In CI

```yaml
# .github/workflows/flakehound.yml
on:
  schedule: [{ cron: "0 3 * * *" }]   # nightly: hunting takes longer than a normal test run
  workflow_dispatch:
permissions:
  contents: read
jobs:
  hunt:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0
        with: { python-version: "3.12" }
      - run: pip install -e . "git+https://github.com/urumb/flakehound"
      - run: flakehound run --json flakehound.json
```

In GitHub Actions the Markdown report is added to the run's summary page automatically.
A ready-made GitHub Action is on the roadmap.

## Quarantine

Found a flaky test you can't fix today? Quarantine it so it stops failing the build,
without hiding it:

```bash
flakehound quarantine add tests/test_cart.py::test_cart_starts_empty --reason "polluted by test_add_item"
flakehound quarantine add --from-report flakehound.json    # every flaky / confirmed latent test
flakehound quarantine list
flakehound quarantine remove tests/test_cart.py::test_cart_starts_empty
```

- The list is one file, `.flakehound/quarantine.toml`: commit it, review it, revert it.
  flakehound never edits your test files.
- Every change shows a diff and asks first (`--yes` to skip the prompt; without a
  terminal and without `--yes`, nothing is written).
- pytest still runs quarantined tests as non-strict `xfail` (or `skip` with
  `mode = "skip"`), so you see `XPASS` once one is fixed. Disable for one run with
  `pytest -p no:flakehound-quarantine`. The plugin does nothing if the file doesn't exist.
- `flakehound run` ignores the quarantine: quarantined tests keep being hunted.

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
5. **Seeds.** For flaky tests that aren't about order, run them alone and swap *one*
   seed at a time between a failing and a passing run: if only the `random` seed flips
   the result it's randomness; if only `PYTHONHASHSEED` does it's set/dict ordering;
   if identical seeds still give different results it's nondeterministic (timing,
   threads, `uuid4`, `os.urandom`...).
6. **Time bomb probes.** Six extra runs with the baseline's exact order and seeds, but
   in UTC+14, UTC−12, two seconds before midnight, on the last day of the month, on
   New Year's Eve and on Feb 29. The clock is *shifted* (it keeps ticking), so code
   that waits on time still works; `monotonic`, `sleep` and timeouts stay real.
7. **Timing probes.** Two more runs: one where every `sleep()` the test makes on its
   main thread is 20× shorter (background threads keep their real speed, like a slow
   machine for whatever the test waits on), and one where threads switch far more
   often (`sys.setswitchinterval`). A test that fails there is *racy*. flakehound then
   shortens one `sleep()` call site at a time to point at the exact line.
8. **Confirm and verify.** Every finding is re-run, and the printed repro command is
   run for real and marked *verified* only if it fails exactly as printed.

## Roadmap

- [x] **M1** Core engine, parallel shuffled runs, terminal + JSON reports, demo
- [x] **M2** Order dependency: isolation, polluter/setter search, *which state leaked*, verified repros
- [x] **M3** Environment: random seed vs hash seed, timezone and danger-date time bombs
- [x] **M4** Timing: sleeps used as waits (exact line) and race conditions
- [x] **M5** Markdown/PR report, GitHub job summary, `--fail-on`, quarantine, pytest-xdist support
- [ ] **M6** First PyPI release
- Later: GitHub Actions history mode, Jest/Go/JUnit adapters, optional local-AI explanations

## Security

flakehound **runs your test code**. Only run it on code you trust. It makes no network
calls, collects no telemetry and never writes environment variables into reports.
See [SECURITY.md](SECURITY.md) to report a vulnerability.

## License

MIT
