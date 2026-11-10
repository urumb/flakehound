import shop


def test_cart_starts_empty():
    # Passes in the default order, fails whenever test_add_item ran first.
    assert shop.cart == []


def test_add_item():
    shop.add_item("apple")  # never cleaned up: leaks into later tests
    assert "apple" in shop.cart
