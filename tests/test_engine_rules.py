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
