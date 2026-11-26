"""Traders turn a market and the council's vote into an intent. They never size in money."""
import numpy as np

from quorum.bots import TRADERS, Consensus, PositionView
from quorum.models import Side
from tests.conftest import series, trend

FLAT, LONG = PositionView(0, 0), PositionView(1, 100)


def decide(key, close, pos=FLAT, direction=0.0, risk_off=0.0, **params):
    return TRADERS[key](params).decide(series(close), pos, Consensus(direction, risk_off))


def crossing_up(n=120):
    """Falls, then turns up so the fast EMA crosses the slow one on the last bar."""
    trader = TRADERS["trend_rider"]()
    close = np.concatenate([trend(80, step=-0.002), trend(40, start=trend(80, step=-0.002)[-1], step=0.004)])
    for end in range(90, len(close) + 1):
        if decide("trend_rider", close[:end], direction=0.5):
            return close[:end]
    raise AssertionError(f"no crossover found for {trader.key}")


def test_rider_buys_a_crossover_only_when_the_council_agrees():
    close = crossing_up()
    assert decide("trend_rider", close, direction=0.5).side == Side.BUY
    assert decide("trend_rider", close, direction=-0.5) is None


def test_rider_exits_when_the_trend_turns():
    intent = decide("trend_rider", trend(120, step=-0.003), pos=LONG)
    assert intent.exit and intent.side == Side.SELL


def test_risk_warnings_shrink_entries():
    close = crossing_up()
    calm = decide("trend_rider", close, direction=0.5)
    wild = decide("trend_rider", close, direction=0.5, risk_off=1.0)
    assert wild.size < calm.size


def dip(n=60, drop=0.08):
    close = np.full(n, 100.0) + np.tile([0.3, -0.3], n // 2)
    close[-1] = 100 * (1 - drop)
    return close


def test_mean_reverter_buys_a_sharp_dip_but_not_in_turbulence():
    assert decide("mean_reverter", dip()).side == Side.BUY
    assert decide("mean_reverter", dip(), risk_off=0.8) is None


def test_mean_reverter_sells_back_at_the_mean():
    close = np.append(dip(), 100.5)
    assert decide("mean_reverter", close, pos=LONG).exit


def test_breakout_buys_a_new_high_and_exits_a_new_low():
    flat = np.full(60, 100.0) + np.tile([0.5, -0.5], 30)
    assert decide("breakout", np.append(flat, 103)).side == Side.BUY
    assert decide("breakout", np.append(flat, 97), pos=LONG).exit
    assert decide("breakout", np.append(flat, 100)) is None
