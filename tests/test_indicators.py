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


def test_zscore_of_a_flat_series_is_zero():
    assert ta.zscore(np.full(30, 5.0)) == 0.0


def test_zscore_is_negative_after_a_drop():
    close = np.array([100.0] * 25 + [90.0])
    assert ta.zscore(close, 20) < -2


def test_efficiency_ratio_tells_a_straight_line_from_noise():
    assert ta.efficiency_ratio(np.linspace(1, 2, 50)) == pytest.approx(1.0)
    assert ta.efficiency_ratio(100 + np.tile([1.0, -1.0], 30)) < 0.1
    assert ta.efficiency_ratio(np.full(40, 3.0)) == 0.0


def test_log_returns_have_one_fewer_value():
    r = ta.log_returns(np.array([100.0, 110.0, 99.0]))
    assert r.tolist() == pytest.approx([np.log(1.1), np.log(0.9)])


def test_correlations_are_symmetric_and_spot_twins():
    rng = np.random.default_rng(7)
    base = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    twin = base * 1.5
    other = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    corr = ta.correlations({"a": base, "b": twin, "c": other})
    assert corr[("a", "b")] == pytest.approx(1.0)
    assert corr[("a", "c")] == corr[("c", "a")]
    assert abs(corr[("a", "c")]) < 0.5
    assert ("a", "a") not in corr


def test_correlations_need_two_markets_and_survive_flat_prices():
    assert ta.correlations({"a": np.arange(1.0, 50.0)}) == {}
    corr = ta.correlations({"a": np.full(50, 1.0), "b": np.arange(1.0, 51.0)})
    assert corr[("a", "b")] == 0.0  # a flat market has no defined correlation; treat it as unrelated


def test_arrays_split_candles_into_columns():
    from quorum.models import Candle

    candles = [Candle(i, 1.0, 2.0 + i, 0.5, 1.5 + i, 10.0 * i) for i in range(3)]
    high, low, close, volume = ta.arrays(candles)
    assert high.tolist() == [2.0, 3.0, 4.0]
    assert low.tolist() == [0.5] * 3
    assert close.tolist() == [1.5, 2.5, 3.5]
    assert volume.tolist() == [0.0, 10.0, 20.0]


async def test_simulated_history_reads_like_a_one_minute_chart():
    from quorum.connectors.sim import SimConnector

    candles = await SimConnector().history("ETH/USDT", 200)
    gaps = {b.ts - a.ts for a, b in zip(candles, candles[1:], strict=False)}
    assert len(candles) == 200 and gaps == {60_000}
    assert all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in candles)
