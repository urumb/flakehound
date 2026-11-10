from datetime import date, datetime, timedelta, timezone


def test_order_date_matches_utc():
    # Bug: mixes local time and UTC. Fails wherever the local date differs from
    # the UTC date (e.g. a CI machine in another timezone, or late evenings).
    assert datetime.now().date() == datetime.now(timezone.utc).date()


def test_coupon_valid_until_tomorrow():
    # Bug: assumes tomorrow is in the same month. False on the last day of every month.
    assert (date.today() + timedelta(days=1)).month == date.today().month


def test_anniversary_next_year():
    today = date.today()
    # Bug: Feb 29 plus one year does not exist -> ValueError on leap days.
    assert today.replace(year=today.year + 1) > today
