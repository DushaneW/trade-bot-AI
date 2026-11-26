"""Connector logic that can be checked without a network: the simulator and ccxt response parsing."""
import pytest

from quorum.connectors import build
from quorum.connectors.crypto import _fees, _reason
from quorum.connectors.sim import SimConnector
from quorum.models import Market, Side


async def test_sim_stop_rests_until_price_crosses_it():
    sim = SimConnector()
    price = (await sim.candles("BTC/USDT", 1))[-1].close
    oid = await sim.place_stop("BTC/USDT", Side.SELL, 0.01, price * 0.5)
    assert (await sim.stop_state("BTC/USDT", oid)).status == "open"
    sim.stops[oid].update(trigger=price * 10, since=0)  # pretend price fell through it
    state = await sim.stop_state("BTC/USDT", oid)
    assert state.status == "filled" and state.qty == 0.01 and state.fee > 0


async def test_sim_cancel_reports_a_fill_that_came_first():
    sim = SimConnector()
    oid = await sim.place_stop("ETH/USDT", Side.SELL, 1, 1.0)
    assert (await sim.cancel_stop("ETH/USDT", oid)).status == "gone"
    oid = await sim.place_stop("ETH/USDT", Side.SELL, 1, 1e9)
    sim.stops[oid]["since"] = 0
    assert (await sim.cancel_stop("ETH/USDT", oid)).status == "filled"
    assert (await sim.stop_state("ETH/USDT", "nope")).status == "gone"


async def test_sim_extras_look_like_exchange_data():
    extra = await SimConnector().extras("SOL/USDT")
    assert set(extra) == {"funding_rate", "oi_change", "oi_minutes", "book_imbalance"}
    assert -1 <= extra["book_imbalance"] <= 1


async def test_sim_history_is_repeatable():
    a = await SimConnector().history("BTC/USDT", 300)
    b = await SimConnector().history("BTC/USDT", 300)
    assert [c.close for c in a] == [c.close for c in b]


def test_build_picks_the_simulator():
    conn = build({"venue": "sim", "market": "crypto"}, {})
    assert isinstance(conn, SimConnector) and conn.market == Market.CRYPTO


def test_reason_keeps_the_human_part_of_an_exchange_error():
    assert _reason(Exception('binance {"code":-2008,"msg":"Invalid Api-Key ID."}')) == "Invalid Api-Key ID."
    assert _reason(Exception("kraken EAPI:Invalid nonce")) == "EAPI:Invalid nonce"


def test_fees_split_between_quote_and_the_bought_coin():
    order = {"fees": [{"cost": 0.0001, "currency": "BTC"}, {"cost": 0.5, "currency": "USDT"}]}
    quote, base = _fees(order, "BTC", "USDT", price=50_000, filled=0.01)
    assert base == pytest.approx(0.0001)
    assert quote == pytest.approx(0.0001 * 50_000 + 0.5)


def test_fees_in_a_third_token_or_missing_use_the_taker_rate():
    bnb = {"fee": {"cost": 0.002, "currency": "BNB"}}
    assert _fees(bnb, "BTC", "USDT", 50_000, 0.01) == pytest.approx((0.5, 0.0))
    assert _fees({}, "BTC", "USDT", 50_000, 0.01) == pytest.approx((0.5, 0.0))
