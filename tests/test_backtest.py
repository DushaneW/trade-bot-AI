"""Backtests replay history through the live components. These tests pin the rules that keep them honest."""
import asyncio

import pytest

from quorum.backtest import (
    FEE,
    MIN_FOLD_BARS,
    SLIPPAGE,
    WARMUP,
    BacktestSpec,
    _align,
    _grid,
    record,
    run_backtest,
    run_walk_forward,
    simulate,
)
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


@pytest.fixture(scope="module")
def tape(history):
    """Recording the council is the slow part, and the same for every trader: do it once."""
    return record(spec(), history)


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


def test_fills_happen_at_the_next_bars_open_with_fees_and_slippage(tape):
    result = simulate(spec(), tape)
    opens = {k: dict(zip(tape.ts, tape.arrays[k][0], strict=True)) for k in tape.keys}
    assert result["trade_log"], "the breakout trader should trade on this history"
    for t in result["trade_log"]:
        if "Stop loss" in t["reason"]:
            continue  # stops fill at the stop price, or worse on a gap
        slip = SLIPPAGE if t["side"] == "buy" else -SLIPPAGE
        assert t["price"] == pytest.approx(opens[t["symbol"]][t["ts"]] * (1 + slip))
        assert t["fee"] == pytest.approx(t["qty"] * t["price"] * FEE)


def test_a_replay_matches_the_record_then_simulate_split(history, tape):
    whole = run_backtest(spec(), history)
    parts = simulate(spec(), tape)
    assert whole["return"] == pytest.approx(parts["return"])
    assert whole["trades"] == parts["trades"]


def test_simulating_a_stretch_starts_with_a_fresh_budget(tape):
    out = simulate(spec(budget=250), tape, start=400, end=600)
    assert out["bars"] == 200
    assert out["equity_curve"][0][1] == pytest.approx(250, rel=0.05)


def test_analysts_that_need_live_exchange_data_sit_backtests_out(tape):
    assert not {"funding", "open_interest", "order_book"} & set(tape.analysts)


def test_walk_forward_tunes_on_one_stretch_and_reports_the_next(history):
    result = run_walk_forward(spec(), history, folds=3)
    wf = result["walk_forward"]
    assert len(wf["folds"]) == 3
    assert wf["settings_tried"] == len(_grid("breakout")) == 9
    for fold in wf["folds"]:
        assert fold["train_start"] < fold["test_start"] < fold["test_end"]
        assert set(fold["params"]) == {"entry", "exit"}
    starts = [f["test_start"] for f in wf["folds"]]
    assert starts == sorted(starts)


def test_walk_forward_refuses_a_short_history(history):
    short = {k: v[: WARMUP + MIN_FOLD_BARS * 2] for k, v in history.items()}
    with pytest.raises(ValueError, match="Walk-forward needs"):
        run_walk_forward(spec(), short)
