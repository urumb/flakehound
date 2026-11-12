## flakehound report

**5 flaky tests, 3 time bombs, 2 racy tests** (10 stable). 15 tests × 12 runs + 8 probe runs + 89 diagnosis runs in 36.0s.

### Flaky tests

Pass sometimes and fail sometimes with no code change.

| Test | Result | Cause |
|---|---|---|
| `tests/test_tags.py::test_tags_render_alphabetically` | failed 10/12 runs (83%) | Set/dict order (hash seed) (confirmed): fails with PYTHONHASHSEED=2795742288 |
| `tests/test_cart.py::test_cart_starts_empty` | failed 4/12 runs (33%) | Order dependency (confirmed): polluted by `test_add_item` |
| `tests/test_currency.py::test_prices_default_to_usd` | failed 4/12 runs (33%) | Order dependency (confirmed): polluted by `test_euro_store` |
| `tests/test_inventory.py::test_apples_in_stock` | failed 4/12 runs (33%) | Order dependency (confirmed): only passes after `test_restock_apples` |
| `tests/test_discount.py::test_lucky_discount_is_small` | failed 1/12 runs (8%) | Randomness (confirmed): fails with random seed 1823296038 |

<details>
<summary><code>tests/test_tags.py::test_tags_render_alphabetically</code>: Set/dict order (hash seed) (confirmed)</summary>

- **Result:** failed 10/12 runs (83%)
- **Evidence:**
  - alone, with a failing run's seeds: failed 4/4; with a passing run's seeds: passed 2/2
  - changing only PYTHONHASHSEED flips the result; changing only the random seed does not (2/2 seed pairs)
- **Fix:** the result depends on set/dict iteration order of strings, which changes with PYTHONHASHSEED: sort before comparing (e.g. `sorted(...)`) or compare as sets
- **Error (10×):** `AssertionError: assert 'python,pytest,flaky' == 'flaky,pytest,python'  (tests/test_tags.py:6)`

Reproduce (verified):

```bash
PYTHONHASHSEED=2795742288 pytest -p no:randomly -p no:cacheprovider -n 0 tests/test_tags.py::test_tags_render_alphabetically
```

</details>

<details>
<summary><code>tests/test_cart.py::test_cart_starts_empty</code>: Order dependency (confirmed)</summary>

- **Result:** failed 4/12 runs (33%)
- **Polluter:** `tests/test_cart.py::test_add_item`
- **Leaked:** `shop.cart: [] → ['apple']`
- **Evidence:**
  - failed in 4/4 runs where test_add_item ran first, 0/8 otherwise
  - passes alone 3/3 with the same seeds; fails 3/3 right after test_add_item
- **Fix:** in test_add_item: reset `shop.cart` around each test, e.g. an autouse fixture that clears it after the test (`yield` then `shop.cart.clear()`)
- **Error (4×):** `AssertionError: assert ['apple'] == []  (tests/test_cart.py:6)`

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 tests/test_cart.py::test_add_item tests/test_cart.py::test_cart_starts_empty
```

</details>

<details>
<summary><code>tests/test_currency.py::test_prices_default_to_usd</code>: Order dependency (confirmed)</summary>

- **Result:** failed 4/12 runs (33%)
- **Polluter:** `tests/test_currency.py::test_euro_store`
- **Leaked:** `env SHOP_CURRENCY: <missing> → 'EUR'`
- **Evidence:**
  - failed in 4/4 runs where test_euro_store ran first, 0/8 otherwise
  - passes alone 3/3 with the same seeds; fails 3/3 right after test_euro_store
- **Fix:** in test_euro_store: use `monkeypatch.setenv`/`monkeypatch.delenv` so `SHOP_CURRENCY` is restored
- **Error (4×):** `AssertionError: assert 'EUR' == 'USD'  (tests/test_currency.py:7)`

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 tests/test_currency.py::test_euro_store tests/test_currency.py::test_prices_default_to_usd
```

</details>

<details>
<summary><code>tests/test_inventory.py::test_apples_in_stock</code>: Order dependency: needs another test (confirmed)</summary>

- **Result:** failed 4/12 runs (33%)
- **Setter:** `tests/test_inventory.py::test_restock_apples`
- **Sets:** `shop.Inventory.stock: {} → {'apple': 5}`
- **Evidence:**
  - passed in 8/8 runs where test_restock_apples ran first, 0/4 otherwise
  - fails alone 3/3; passes 3/3 right after test_restock_apples
- **Fix:** test_apples_in_stock relies on state created by test_restock_apples; give it its own setup (a fixture) so it passes on its own
- **Error (4×):** `AssertionError: assert 0 > 0  (tests/test_inventory.py:11)`

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 tests/test_inventory.py::test_apples_in_stock
```

</details>

<details>
<summary><code>tests/test_discount.py::test_lucky_discount_is_small</code>: Randomness (confirmed)</summary>

- **Result:** failed 1/12 runs (8%)
- **Evidence:**
  - alone, with a failing run's seeds: failed 2/2; with a passing run's seeds: passed 1/1
  - changing only the random seed flips the result; changing only PYTHONHASHSEED does not (1/1 seed pairs)
- **Fix:** the test depends on unseeded randomness: seed it (`random.seed(...)` or pass a `random.Random(seed)` into the code) or assert on properties true for every value
- **Error (1×):** `assert 0.9086672741742468 < 0.8  (tests/test_discount.py:5)`

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 -p flakehound.adapters.pytest.plugin --flakehound-seed=1823296038 tests/test_discount.py::test_lucky_discount_is_small
```

</details>

### Time bombs

Pass today, but fail on certain dates or in other timezones.

| Test | Result | Cause |
|---|---|---|
| `tests/test_dates.py::test_anniversary_next_year` | passes today, but fails on a leap day (Feb 29) | Date/clock (confirmed) |
| `tests/test_dates.py::test_coupon_valid_until_tomorrow` | passes today, but fails on the last day of the month, on New Year's Eve, two seconds before midnight and on a leap day (Feb 29) | Date/clock (confirmed) |
| `tests/test_dates.py::test_order_date_matches_utc` | passes today, but fails in timezone UTC+14 | Timezone (confirmed) |

<details>
<summary><code>tests/test_dates.py::test_anniversary_next_year</code>: Date/clock (confirmed)</summary>

- **Result:** passes today, but fails on a leap day (Feb 29)
- **Evidence:**
  - passed in the normal run, failed in the same run with only the clock changed
  - alone, clock at 2028-02-29 12:00:00: failed 2/2
  - alone, with the real clock and timezone: passed
- **Fix:** date arithmetic breaks on a leap day (Feb 29): use `timedelta` (or `dateutil.relativedelta`) instead of editing day/month/year fields directly, and test with fixed dates

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 -p flakehound.adapters.pytest.plugin --flakehound-clock=2028-02-29T12:00:00 tests/test_dates.py::test_anniversary_next_year
```

</details>

<details>
<summary><code>tests/test_dates.py::test_coupon_valid_until_tomorrow</code>: Date/clock (confirmed)</summary>

- **Result:** passes today, but fails on the last day of the month, on New Year's Eve, two seconds before midnight and on a leap day (Feb 29)
- **Evidence:**
  - passed in the normal run, failed in the same run with only the clock changed
  - alone, clock at 2026-11-30 12:00:00: failed 2/2
  - alone, clock at 2026-12-31 23:59:58: failed 2/2
  - alone, clock at 2028-02-29 12:00:00: failed 2/2
  - alone, with the real clock and timezone: passed
- **Fix:** date arithmetic breaks on the last day of the month: use `timedelta` (or `dateutil.relativedelta`) instead of editing day/month/year fields directly, and test with fixed dates

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 -p flakehound.adapters.pytest.plugin --flakehound-clock=2026-11-30T12:00:00 tests/test_dates.py::test_coupon_valid_until_tomorrow
```

</details>

<details>
<summary><code>tests/test_dates.py::test_order_date_matches_utc</code>: Timezone (confirmed)</summary>

- **Result:** passes today, but fails in timezone UTC+14
- **Evidence:**
  - passed in the normal run, failed in the same run with only the timezone changed
  - alone, in timezone UTC+14: failed 2/2
  - alone, with the real clock and timezone: passed
  - alone, with TZ set to this machine's own UTC offset (FHT-5:30): failed 0/2
- **Fix:** the code mixes local time and UTC (e.g. `datetime.now()` or `date.today()` next to UTC values): use timezone-aware datetimes consistently, e.g. `datetime.now(timezone.utc)`

Reproduce (verified):

```bash
TZ=FHT-14 pytest -p no:randomly -p no:cacheprovider -n 0 tests/test_dates.py::test_order_date_matches_utc
```

</details>

### Racy tests

Pass normally, but fail on a slower machine or when threads interleave.

| Test | Result | Cause |
|---|---|---|
| `tests/test_upload.py::test_upload_finishes` | passes normally, but fails when the `sleep(0.15)` at tests/test_upload.py:9 is shorter (a slower machine) | Timing (sleep used as a wait) (confirmed) |
| `tests/test_wallet.py::test_concurrent_top_ups` | passes normally, but fails when threads switch more often (a race condition) | Race condition (confirmed) |

<details>
<summary><code>tests/test_upload.py::test_upload_finishes</code>: Timing (sleep used as a wait) (confirmed)</summary>

- **Result:** passes normally, but fails when the `sleep(0.15)` at tests/test_upload.py:9 is shorter (a slower machine)
- **Evidence:** alone, main-thread sleeps 20x shorter: failed 3/3; normal: failed 0/2
- **Fix:** `sleep(0.15)` at tests/test_upload.py:9 is used to wait for work that isn't guaranteed to be finished; wait for the actual event instead: `thread.join(timeout)`, `threading.Event().wait(timeout)`, or poll the condition with a deadline

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 -p flakehound.adapters.pytest.plugin --flakehound-sleep-scale=0.05 --flakehound-sleep-sites=tests/test_upload.py:9 tests/test_upload.py::test_upload_finishes
```

</details>

<details>
<summary><code>tests/test_wallet.py::test_concurrent_top_ups</code>: Race condition (confirmed)</summary>

- **Result:** passes normally, but fails when threads switch more often (a race condition)
- **Evidence:** alone, with frequent thread switches: failed 4/4; normal: failed 0/2
- **Fix:** threads share state without proper locking: protect read-modify-write sequences with a `threading.Lock`, use thread-safe structures like `queue.Queue`, and join threads before asserting

Reproduce (verified):

```bash
pytest -p no:randomly -p no:cacheprovider -n 0 -p flakehound.adapters.pytest.plugin --flakehound-switch-interval=1e-06 tests/test_wallet.py::test_concurrent_top_ups
```

</details>

<sub>flakehound 0.1.3 · replay this session: `flakehound run --seed 7 -n 12`</sub>
