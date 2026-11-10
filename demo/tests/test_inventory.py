import shop


def test_restock_apples():
    shop.Inventory.restock("apple", 5)
    assert shop.Inventory.stock["apple"] >= 5


def test_apples_in_stock():
    # Bug: silently relies on test_restock_apples having run first.
    assert shop.Inventory.stock.get("apple", 0) > 0
