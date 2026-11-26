"""Analysts read a Series and say what they see. Each is checked on a market shaped to make its point."""
import numpy as np

from quorum.bots import ANALYSTS
from quorum.bots.analysts import price_str
from tests.conftest import series, trend


def read(key: str, close, **params):
    return ANALYSTS[key](params).read(series(close))


def test_trend_leans_with_the_trend():
    assert read("trend", trend(800, step=0.002)).direction > 0.5
    assert read("trend", trend(800, step=-0.002)).direction < -0.5


def test_trend_uses_the_longer_chart_once_there_is_history():
    short = read("trend", trend(150))
    long = read("trend", trend(800))
    assert "12x" not in short.note and "12x" in long.note


def test_trend_warns_when_the_short_chart_fights_the_big_one():
    close = np.concatenate([trend(700, step=0.003), trend(100, start=trend(700, step=0.003)[-1], step=-0.004)])
    r = read("trend", close)
    assert "against the bigger trend" in r.note


def test_price_str_keeps_useful_precision():
    assert price_str(64123.456) == "64,123.46"
    assert price_str(1.08512) == "1.0851"
    assert price_str(0.0001234) == "0.000123"


def test_momentum_flags_overbought_and_oversold():
    up = read("momentum", np.linspace(100, 160, 120))
    down = read("momentum", np.linspace(160, 100, 120))
    assert up.direction < 0 and "Overbought" in up.note
    assert down.direction > 0 and "Oversold" in down.note


def test_divergence_spots_a_higher_high_on_weaker_momentum():
    # A sharp rally, a pullback, then a slow grind to a marginally higher high.
    close = np.concatenate([np.full(40, 100.0), np.linspace(100, 120, 8), np.linspace(120, 110, 8),
                            np.linspace(110, 121, 14)])
    r = read("divergence", close)
    assert r.direction < 0 and "Bearish divergence" in r.note


def test_divergence_is_quiet_on_a_clean_trend():
    assert read("divergence", trend(120)).direction == 0.0


def test_volatility_warns_when_ranges_blow_out():
    close = np.full(150, 100.0)
    calm = ANALYSTS["volatility"]().read(series(close, spread=0.001))
    s = series(close, spread=0.001)
    s.high[-5:] *= 1.05
    s.low[-5:] *= 0.95
    wild = ANALYSTS["volatility"]().read(s)
    assert calm.kind == wild.kind == "risk"
    assert wild.direction == -1.0 and "reduce risk" in wild.note


def test_volume_confirms_a_move_on_heavy_volume():
    s = series(np.concatenate([np.full(60, 100.0), np.linspace(100, 104, 5)]))
    s.volume[-5:] = 400
    r = ANALYSTS["volume"]().read(s)
    assert r.direction > 0 and "Volume" in r.note


def test_volume_distrusts_a_move_on_thin_volume():
    s = series(np.concatenate([np.full(60, 100.0), np.linspace(100, 104, 5)]))
    s.volume[-5:] = 20
    r = ANALYSTS["volume"]().read(s)
    assert r.direction < 0 and "thin volume" in r.note


def test_volume_stays_silent_without_history():
    assert ANALYSTS["volume"]().read(series(np.full(20, 1.0))) is None


def test_regime_calls_a_clean_trend_and_a_chop():
    assert "Clean up" in read("regime", trend(100)).note
    assert "Choppy" in read("regime", 100 + np.tile([1.0, -1.0], 50)).note


def test_structure_leans_up_near_support_and_down_near_resistance():
    swing = 100 + 10 * np.sin(np.linspace(0, 6 * np.pi, 150))
    low = read("structure", np.append(swing, 91.5))
    high = read("structure", np.append(swing, 108.5))
    assert low.direction > 0 and "support" in low.note
    assert high.direction < 0 and "resistance" in high.note


def test_structure_reports_a_range_test():
    swing = 100 + 10 * np.sin(np.linspace(0, 6 * np.pi, 150))
    assert "Testing range high" in read("structure", np.append(swing, 115)).note


def cross(key: str, closes: dict, **params):
    return ANALYSTS[key](params).read_all({k: series(c) for k, c in closes.items()})


def test_breadth_and_ranking_need_at_least_two_markets():
    for key in ("breadth", "relative", "correlation"):
        assert cross(key, {"a": trend(200)}) == {}


def test_ranking_puts_the_strongest_market_first():
    out = cross("relative", {"fast": trend(200, step=0.004), "slow": trend(200, step=0.001),
                             "down": trend(200, step=-0.002)})
    assert out["fast"].direction > out["slow"].direction > out["down"].direction
    assert out["fast"].note.startswith("Rank 1 of 3")


def test_breadth_leans_up_when_most_markets_rise():
    out = cross("breadth", {"a": trend(200), "b": trend(200, step=0.003), "c": trend(200, step=-0.001)})
    assert out["a"].direction > 0 and "67%" in out["a"].note


def test_correlation_spots_a_laggard_that_has_not_caught_up():
    rng = np.random.default_rng(3)
    leader = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    follower = leader * 0.05 * np.exp(rng.normal(0, 0.001, 200))
    follower[-10:] = follower[-11]  # the leader kept moving, the follower stood still
    leader[-10:] = leader[-11] * np.linspace(1.002, 1.03, 10)
    out = cross("correlation", {"BTC": leader, "SOL": follower})
    assert out["SOL"].direction > 0 and "Lagging BTC" in out["SOL"].note


def test_correlation_stays_quiet_on_unrelated_markets():
    rng = np.random.default_rng(4)
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    b = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    out = cross("correlation", {"a": a, "b": b})
    assert out["a"].direction == 0.0 and "on its own" in out["a"].note
