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
