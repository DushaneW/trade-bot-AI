"""The risk manager is the only path from an idea to an order."""
import pytest

from quorum.ledger import Ledger
from quorum.models import Fill, Market, Mode, Side
from quorum.risk import PROFILES, RiskLimits, RiskManager, conviction

KEY, OTHER = "acc:BTC/USDT", "acc:ETH/USDT"


def holding(qty: float, price: float, budget: float = 100.0, key: str = KEY) -> Ledger:
    led = Ledger(budget)
    led.apply(Fill("o", "b", key, Side.BUY, qty, price, 0.0, Mode.LIVE, "t"))
    return led


def test_drawdown_pauses_until_resumed_and_daily_loss_for_the_day():
    risk, limits = RiskManager(), RiskLimits()
    led = Ledger(100.0)
    assert risk.check_bot(led, limits, 100, 100) is None
    kind, reason = risk.check_bot(led, limits, 69, 100)
    assert kind == "drawdown" and "Drawdown" in reason
    kind, _ = risk.check_bot(led, limits, 89, 100)
    assert kind == "daily"


def test_daily_limit_ignored_before_the_day_has_a_start():
    assert RiskManager().check_bot(Ledger(100.0), RiskLimits(), 85, 0) is None


def test_stop_loss_fires_at_the_limit_for_longs_and_shorts():
    risk, limits = RiskManager(), RiskLimits(stop_loss_pct=0.04)
    long = holding(1, 100)
    assert risk.stop_triggered(long, limits, KEY, 96.5) is None
    assert "Stop loss" in risk.stop_triggered(long, limits, KEY, 96.0)
    short = Ledger(100.0)
    short.apply(Fill("o", "b", KEY, Side.SELL, 1, 100, 0.0, Mode.LIVE, "t"))
    assert risk.stop_triggered(short, limits, KEY, 103) is None
    assert risk.stop_triggered(short, limits, KEY, 104.5)


def test_trailing_stop_follows_the_peak_and_only_locks_in_gains():
    risk = RiskManager()
    limits = RiskLimits(stop_loss_pct=0.5, trailing_stop_pct=0.05)
    led = holding(1, 100)
    assert risk.stop_triggered(led, limits, KEY, 120) is None   # new peak
    assert risk.stop_triggered(led, limits, KEY, 115) is None   # 4.2% off the peak
    assert "Trailing" in risk.stop_triggered(led, limits, KEY, 113)


def test_no_stop_without_a_position():
    assert RiskManager().stop_triggered(Ledger(100.0), RiskLimits(), KEY, 1.0) is None
