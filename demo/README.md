# flakehound demo

A tiny shop whose tests are flaky on purpose. Each file is one classic trap:

| Test file | Trap |
|---|---|
| `tests/test_cart.py` | Order dependency: `test_add_item` leaves an item in the shared cart |
| `tests/test_discount.py` | Unseeded randomness |
| `tests/test_tags.py` | Set iteration order depends on `PYTHONHASHSEED` |
| `tests/test_upload.py` | Timing: `sleep()` used to wait for a background thread |
| `tests/test_math.py` | Stable tests, for contrast |

Run `flakehound run` in this folder.
