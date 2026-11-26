"""Backtests replay history through the live components. These tests pin the rules that keep them honest."""
import asyncio

import pytest

from quorum.backtest import (FEE, MIN_FOLD_BARS, SLIPPAGE, WARMUP, BacktestSpec, _align, _grid, record,
                             run_backtest, run_walk_forward, simulate)
from quorum.connectors.sim import SimConnector
from quorum.models import Candle, Market


@pytest.fixture(scope="module")
def history():
    sim = SimConnector()
    return asyncio.run(_load(sim))


async def _load(sim):
    return {s: await sim.history(s, 900) for s in ("BTC/USDT", "ETH/USDT")}


def spec(strategy="breakout", **kw):
    return BacktestSpec(strategy, ["BTC/USDT", "ETH/USDT"], Market.CRYPTO, **kw)


def test_align_keeps_only_shared_timestamps():
    a = [Candle(t, 1, 1, 1, 1, 1) for t in (1, 2, 3)]
    b = [Candle(t, 1, 1, 1, 1, 1) for t in (2, 3, 4)]
    ts, data = _align({"a": a, "b": b})
    assert ts == [2, 3]
    assert [c.ts for c in data["a"]] == [2, 3]


def test_too_little_history_is_refused():
    short = {"X": [Candle(i, 1, 1, 1, 1, 1) for i in range(50)]}
    with pytest.raises(ValueError, match="need at least"):
        record(spec(), short)


def test_fills_happen_at_the_next_bars_open_with_fees_and_slippage(history):
    result = run_backtest(spec(), history)
    tape = record(spec(), history)
    opens = {k: dict(zip(tape.ts, tape.arrays[k][0], strict=True)) for k in tape.keys}
    assert result["trade_log"], "the breakout trader should trade on this history"
    for t in result["trade_log"]:
        if "Stop loss" in t["reason"]:
            continue  # stops fill at the stop price, or worse on a gap
        slip = SLIPPAGE if t["side"] == "buy" else -SLIPPAGE
        assert t["price"] == pytest.approx(opens[t["symbol"]][t["ts"]] * (1 + slip))
        assert t["fee"] == pytest.approx(t["qty"] * t["price"] * FEE)
