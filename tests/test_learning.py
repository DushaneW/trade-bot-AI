"""How the council learns whom to trust."""
import pytest

from quorum.bots import Reading
from quorum.learning import MAX_WEIGHT, MIN_WEIGHT, Council, Reputation, regime_of, weight_of

KEY, BAR = "acc:BTC/USDT", 60_000


def test_a_new_analyst_starts_neutral():
    rep = Reputation()
    assert rep.accuracy == 0.5
    assert rep.weight == 1.0


def test_weight_is_clamped():
    assert weight_of(1.0) == MAX_WEIGHT
    assert weight_of(0.0) == MIN_WEIGHT


def test_history_keeps_the_last_forty_calls():
    rep = Reputation()
    for i in range(50):
        rep.record(i % 2 == 0)
    assert rep.calls == 50 and len(rep.history) == 40
    assert Reputation.from_dict(rep.to_dict()).hits == rep.hits


def test_shrinkage_pulls_a_short_record_toward_the_overall_score():
    lucky = Reputation(hits=3, calls=3)
    assert lucky.shrunk(0.5) < 0.7  # three right calls are not proof
    seasoned = Reputation(hits=90, calls=100)
    assert seasoned.shrunk(0.5) > 0.8


def test_regime_threshold():
    assert regime_of(0.5) == "trend"
    assert regime_of(0.2) == "chop"


def test_calls_are_scored_after_the_horizon_per_market_and_regime():
    council = Council(horizon_bars=8)
    council.hear("vector", KEY, Reading("trend", 0.8, 0.9, "up"), 100.0, ts=0, bar_ms=BAR, atr_frac=0.01, regime="trend")
    assert council.score({KEY: 105.0}, now=BAR * 7) == []  # not due yet
    assert council.score({KEY: 105.0}, now=BAR * 8) == [("vector", True)]
    assert council.rep("vector").hits == 1
    assert council.rep("vector", f"m:{KEY}").hits == 1
    assert council.rep("vector", "r:trend").hits == 1
    assert council.pending == []


def test_neutral_readings_are_not_predictions():
    council = Council()
    council.hear("x", KEY, Reading("trend", 0.05, 0.5, "flat"), 100.0, 0, BAR, 0.01)
    assert council.pending == []


def test_risk_warnings_are_judged_on_the_size_of_the_move():
    council = Council(horizon_bars=4)
    warn = Reading("risk", -1.0, 0.8, "wild")
    council.hear("seismo", KEY, warn, 100.0, 0, BAR, atr_frac=0.01)  # typical move 2% over 4 bars
    council.hear("seismo", "acc:ETH", warn, 100.0, 0, BAR, atr_frac=0.01)
    results = council.score({KEY: 95.0, "acc:ETH": 100.5}, now=BAR * 4)
    assert sorted(results) == [("seismo", False), ("seismo", True)]  # BTC moved 5%: right; ETH 0.5%: wrong
