"""Indicators are pure functions of numpy arrays, so they can be checked against hand-worked values."""
import numpy as np
import pytest

from quorum import indicators as ta


def test_ema_starts_at_first_value_and_converges():
    x = np.array([10.0] + [20.0] * 200)
    e = ta.ema(x, 10)
    assert e[0] == 10.0
    assert e[-1] == pytest.approx(20.0, abs=1e-6)
    assert np.all(np.diff(e) >= 0)  # never overshoots a step up


def test_ema_weights_recent_values_more_than_sma():
    x = np.array([1.0] * 50 + [2.0] * 5)
    assert ta.ema(x, 20)[-1] > ta.sma(x, 20)[-1]


def test_sma_is_nan_until_the_window_fills():
    s = ta.sma(np.arange(1.0, 6.0), 3)
    assert np.isnan(s[:2]).all()
    assert s[2:].tolist() == [2.0, 3.0, 4.0]
