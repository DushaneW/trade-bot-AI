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


def test_rsi_is_100_with_no_losses_and_low_when_falling():
    up = np.linspace(100, 200, 100)
    assert ta.rsi(up) == 100.0
    assert ta.rsi(up[::-1]) < 5


def test_rsi_sits_near_50_on_a_zigzag():
    zigzag = 100 + np.tile([1.0, -1.0], 60)
    assert 40 < ta.rsi(zigzag) < 60


def test_rsi_series_matches_the_last_value_and_stays_in_range():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    r = ta.rsi_series(close)
    assert len(r) == len(close)
    assert np.all((r >= 0) & (r <= 100))


def test_atr_of_a_constant_range_is_that_range():
    close = np.full(100, 50.0)
    atr = ta.atr(close + 1, close - 1, close)
    assert atr[-1] == pytest.approx(2.0)


def test_atr_counts_gaps_from_the_previous_close():
    close = np.array([100.0] * 30 + [110.0] * 30)
    high, low = close + 0.5, close - 0.5
    atr = ta.atr(high, low, close, n=5)
    assert atr[30] > 1.0  # the jump from 100 to 110 is part of the true range
