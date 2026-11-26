"""The engine end to end on the simulator: teams, orders, backup stops and restarts."""
import pytest

from quorum.engine import ConfigError
from quorum.models import Side
from tests.conftest import SYMBOLS


async def test_team_hires_every_analyst_that_fits_the_market(team):
    engine, _ = team
    keys = {b.config["strategy"] for b in engine.bots.values() if not b.is_trader}
    assert {"funding", "order_book", "correlation", "trend"} <= keys
    traders = [b for b in engine.bots.values() if b.is_trader]
    assert [b.ledger.budget for b in traders] == [200, 200]


async def test_a_second_team_or_a_tiny_budget_is_refused(engine):
    acc = await engine.add_account({"market": "crypto", "venue": "sim", "symbols": SYMBOLS})
    with pytest.raises(ConfigError, match="too little"):
        engine.create_team(acc.id, SYMBOLS, 20, ["trend_rider", "breakout"])
    engine.create_team(acc.id, SYMBOLS, 300)
    with pytest.raises(ConfigError, match="already exists"):
        engine.create_team(acc.id, SYMBOLS, 300)


async def test_nothing_runs_before_start(engine):
    assert not engine.running
    with pytest.raises(ConfigError, match="no team"):
        engine.start_council()


async def test_stopping_can_close_every_position(team):
    engine, acc = team
    rider = next(b for b in engine.bots.values() if b.config["strategy"] == "trend_rider")
    key = f"{acc.id}:BTC/USDT"
    await engine._execute(rider, key, Side.BUY, 50 / engine.prices[key], "test")
    await engine.stop_council(close_positions=True)
    assert not engine.running
    assert not rider.ledger.position(key).is_open
