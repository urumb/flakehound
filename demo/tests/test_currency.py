import os

import shop


def test_prices_default_to_usd():
    assert shop.currency() == "USD"


def test_euro_store():
    os.environ["SHOP_CURRENCY"] = "EUR"  # bug: never restored (use monkeypatch.setenv)
    assert shop.currency() == "EUR"
