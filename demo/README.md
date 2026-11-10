# flakehound demo

A tiny shop whose tests are flaky on purpose. Each file is one classic trap:

| Test file | Trap |
|---|---|
| `tests/test_cart.py` | Order dependency: `test_add_item` leaves an item in the shared cart |
| `tests/test_currency.py` | Order dependency: `test_euro_store` leaks the `SHOP_CURRENCY` env var |
| `tests/test_inventory.py` | Needs another test: `test_apples_in_stock` only passes after `test_restock_apples` |
| `tests/test_discount.py` | Unseeded randomness |
| `tests/test_tags.py` | Set iteration order depends on `PYTHONHASHSEED` |
| `tests/test_upload.py` | Timing: `sleep()` used to wait for a background thread |
| `tests/test_dates.py` | Time bombs: mixes local time and UTC, breaks on the last day of the month and on Feb 29 |
| `tests/test_math.py` | Stable tests, for contrast |

Run `flakehound run` in this folder.
