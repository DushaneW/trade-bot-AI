"""Engine rules around traders: the kill switch, pauses, edits and bookkeeping details."""
import pytest

from quorum.connectors.base import ExecResult
from quorum.engine import ConfigError
from quorum.models import Side


def trader(engine, strategy="trend_rider"):
    return next(b for b in engine.bots.values() if b.config["strategy"] == strategy)


async def hold(engine, acc, symbol="BTC/USDT", money=60.0, strategy="trend_rider"):
    bot, key = trader(engine, strategy), f"{acc.id}:{symbol}"
    await engine._execute(bot, key, Side.BUY, money / engine.prices[key], "test")
    return bot, key


async def test_kill_switch_blocks_new_orders_and_can_flatten(team):
    engine, acc = team
    bot, key = await hold(engine, acc)
    await engine.halt(True)
    await engine._execute(bot, f"{acc.id}:ETH/USDT", Side.BUY, 0.01, "blocked")
    rejected = [m["data"] for m in engine.bus.history if m["topic"] == "rejected"]
    assert rejected and rejected[-1]["reason"] == "Kill switch is on"
    await engine.halt(True, flatten=True)
    assert not bot.ledger.position(key).is_open  # closing is forced through the switch
    await engine.halt(False)
    assert not engine.risk.halted


async def test_a_drawdown_pause_closes_positions_and_waits_for_the_owner(team):
    engine, acc = team
    bot, key = await hold(engine, acc)
    bot.ledger.cash -= bot.ledger.budget * 0.5  # pretend half the budget is gone
    await engine._tick_trader(bot, {}, 0)
    assert bot.paused_kind == "drawdown" and "Drawdown" in bot.paused_reason
    assert not bot.ledger.position(key).is_open
    engine.update_bot(bot.id, {"enabled": True})
    assert bot.paused_reason is None


async def test_a_daily_pause_lifts_on_the_next_day(team):
    engine, acc = team
    bot = trader(engine)
    bot.day, bot.paused_kind, bot.paused_reason = "2000-01-01", "daily", "Daily loss limit hit"
    await engine._tick_trader(bot, {}, 0)
    assert bot.paused_reason is None and bot.day != "2000-01-01"


async def test_stop_losses_still_work_while_the_council_is_stopped(team):
    engine, acc = team
    bot, key = await hold(engine, acc)
    await engine.stop_council(close_positions=False)
    bot.ledger.position(key).avg_price *= 1.10  # bought 10% higher than the price now: past the 4% stop
    await engine._guard_open_positions(acc)
    assert not bot.ledger.position(key).is_open
    assert engine.store.fills(bot.id)[0]["reason"].startswith("Stop loss")
