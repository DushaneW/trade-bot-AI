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
