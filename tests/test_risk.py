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


def review(led, side=Side.BUY, qty=1.0, price=10.0, equity=None, limits=None, market=Market.CRYPTO, **kw):
    return RiskManager().review(led, limits or RiskLimits(), market, KEY, side, qty, price,
                                led.cash if equity is None else equity, **kw)


def test_entries_are_capped_by_the_position_limit_and_the_cash():
    v = review(Ledger(100.0), qty=8, price=10)  # 80 wanted, 50% cap
    assert v.qty * 10 == pytest.approx(50) and "position size" in v.reason
    led = Ledger(100.0)
    led.cash = 20
    v = review(led, qty=4, price=10, equity=100)
    assert v.qty * 10 < 20 and "available budget" in v.reason


def test_exits_are_always_allowed_but_never_bigger_than_the_position():
    led = holding(2, 10)
    v = review(led, side=Side.SELL, qty=5)
    assert v.qty == 2 and v.reason is None


def test_shorting_is_forex_only():
    assert not review(Ledger(100.0), side=Side.SELL).approved
    assert review(Ledger(100.0), side=Side.SELL, qty=1, market=Market.FOREX).approved


def test_kill_switch_refuses_everything():
    risk = RiskManager()
    risk.halted = True
    v = risk.review(holding(1, 10), RiskLimits(), Market.CRYPTO, KEY, Side.SELL, 1, 10, 100)
    assert not v.approved and "Kill switch" in v.reason
