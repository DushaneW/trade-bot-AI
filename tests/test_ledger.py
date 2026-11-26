"""Each trader's own books: cash, positions, fees and realised results."""
import pytest

from quorum.ledger import Ledger, Position
from quorum.models import Fill, Mode, Side

KEY = "acc:BTC/USDT"


def fill(side: Side, qty: float, price: float, fee: float = 0.0, key: str = KEY) -> Fill:
    return Fill("ord", "bot", key, side, qty, price, fee, Mode.LIVE, "test")


def test_buy_moves_cash_into_a_position():
    led = Ledger(100.0)
    led.apply(fill(Side.BUY, 0.001, 50_000, fee=0.05))
    pos = led.position(KEY)
    assert pos.qty == pytest.approx(0.001)
    assert pos.avg_price == 50_000
    assert led.cash == pytest.approx(100 - 50 - 0.05)
    assert led.equity({KEY: 50_000}) == pytest.approx(99.95)


def test_adding_to_a_position_averages_the_price():
    led = Ledger(1000.0)
    led.apply(fill(Side.BUY, 1, 100))
    led.apply(fill(Side.BUY, 1, 200))
    assert led.position(KEY).avg_price == pytest.approx(150)


def test_selling_realises_the_result_net_of_the_exit_fee():
    led = Ledger(1000.0)
    led.apply(fill(Side.BUY, 2, 100, fee=0.2))
    pnl = led.apply(fill(Side.SELL, 2, 110, fee=0.22))
    assert pnl == pytest.approx(2 * 10 - 0.22)
    assert not led.position(KEY).is_open
    assert led.trades == 1 and led.wins == 1
    assert led.cash == pytest.approx(1000 - 200 - 0.2 + 220 - 0.22)
    assert led.fees == pytest.approx(0.42)


def test_partial_sell_keeps_the_average_price():
    led = Ledger(1000.0)
    led.apply(fill(Side.BUY, 4, 100))
    led.apply(fill(Side.SELL, 1, 120))
    pos = led.position(KEY)
    assert pos.qty == pytest.approx(3)
    assert pos.avg_price == 100
