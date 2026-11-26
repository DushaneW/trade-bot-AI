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
