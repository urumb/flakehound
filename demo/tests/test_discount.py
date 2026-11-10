import shop


def test_lucky_discount_is_small():
    assert shop.lucky_discount() < 0.8  # fails about 20% of the time
