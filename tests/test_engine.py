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


def rider_of(engine):
    return next(b for b in engine.bots.values() if b.config["strategy"] == "trend_rider")


async def buy(engine, acc, symbol="BTC/USDT", money=60.0):
    rider, key = rider_of(engine), f"{acc.id}:{symbol}"
    await engine._execute(rider, key, Side.BUY, money / engine.prices[key], "test entry")
    return rider, key, rider.ledger.position(key)


async def test_a_buy_places_a_backup_stop_past_the_traders_own(team):
    engine, acc = team
    rider, key, pos = await buy(engine, acc)
    assert pos.stop_id and pos.stop_qty == pytest.approx(pos.qty)
    expected = pos.avg_price * (1 - rider.limits.stop_loss_pct - 0.005)
    assert pos.stop_price == pytest.approx(expected)


async def test_an_exit_cancels_the_stop_before_selling(team):
    engine, acc = team
    rider, key, pos = await buy(engine, acc)
    stop_id = pos.stop_id
    await engine._execute(rider, key, Side.SELL, pos.qty, "test exit")
    assert not rider.ledger.position(key).is_open
    assert acc.connector.stops[stop_id]["status"] == "gone"


async def test_a_stop_that_filled_while_away_is_booked(team):
    engine, acc = team
    rider, key, pos = await buy(engine, acc)
    acc.connector.stops[pos.stop_id].update(trigger=engine.prices[key] * 10, since=0)
    await engine._check_stops(acc)
    fills = engine.store.fills(rider.id)
    assert fills[0]["reason"] == "Backup stop filled on the exchange"
    assert not rider.ledger.position(key).is_open


async def test_a_stop_removed_on_the_exchange_is_replaced(team):
    engine, acc = team
    _, _, pos = await buy(engine, acc)
    old = pos.stop_id
    acc.connector.stops[old]["status"] = "gone"
    await engine._check_stops(acc)
    assert pos.stop_id and pos.stop_id != old


async def test_turning_backup_stops_off_cancels_them(team):
    engine, acc = team
    _, _, pos = await buy(engine, acc)
    old = pos.stop_id
    await engine.set_exchange_stops(False)
    assert not pos.stop_id and acc.connector.stops[old]["status"] == "gone"
    assert engine.store.setting("exchange_stops") is False


async def test_correlated_second_entry_is_trimmed(team):
    engine, acc = team
    btc, eth = f"{acc.id}:BTC/USDT", f"{acc.id}:ETH/USDT"
    await buy(engine, acc, "BTC/USDT", money=90)
    engine.correlation[acc.id][(eth, btc)] = 0.9  # simulated markets are independent; make these move as one
    rider = rider_of(engine)
    await engine._execute(rider, eth, Side.BUY, 90 / engine.prices[eth], "test")
    fill = engine.store.fills(rider.id)[0]
    assert "move with this one" in fill["reason"]
    assert fill["qty"] * fill["price"] < 90


async def test_positions_and_stops_survive_a_restart(team, tmp_path, bus):
    from quorum.engine import Engine
    from quorum.security import Vault
    from quorum.storage import Store

    engine, acc = team
    rider, key, pos = await buy(engine, acc)
    engine._persist()
    again = Engine(Store(tmp_path / "q.db"), Vault(tmp_path), bus)
    await again.start()
    again._task.cancel()
    restored = again.bots[rider.id].ledger.position(key)
    assert restored.qty == pytest.approx(pos.qty) and restored.stop_id == pos.stop_id
    assert again.running  # the owner had started the council before the restart
    await again.stop()
