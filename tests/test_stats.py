import pytest

from flakehound.core.stats import wilson_interval


def test_wilson_known_value():
    lo, hi = wilson_interval(3, 10)
    assert lo == pytest.approx(0.1078, abs=1e-3)
    assert hi == pytest.approx(0.6032, abs=1e-3)


def test_wilson_edges():
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_rejects_bad_input():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)
