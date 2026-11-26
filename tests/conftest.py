"""Shared fixtures. Everything runs against the built-in simulated market: no network, no keys."""
from __future__ import annotations

import numpy as np
import pytest

from quorum.bots import Series
from quorum.bus import Bus
from quorum.engine import Engine
from quorum.security import Vault
from quorum.storage import Store

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def series(close, spread: float = 0.002, volume: float = 100.0) -> Series:
    """A Series from closes, with highs and lows a little either side."""
    close = np.asarray(close, dtype=float)
    return Series(close * (1 + spread), close * (1 - spread), close, np.full(len(close), volume))


def trend(n: int = 300, start: float = 100.0, step: float = 0.002) -> np.ndarray:
    """A steady compounding trend; negative steps trend down."""
    return start * np.cumprod(np.full(n, 1 + step))


@pytest.fixture
def bus() -> Bus:
    return Bus(history=2000)


@pytest.fixture
async def engine(tmp_path, bus):
    """An engine with its loop stopped, so tests drive it step by step."""
    eng = Engine(Store(tmp_path / "q.db"), Vault(tmp_path), bus)
    await eng.start()
    eng._task.cancel()
    yield eng
    await eng.stop()


@pytest.fixture
async def team(engine):
    """A simulated account with the full team, the council running and prices loaded."""
    acc = await engine.add_account({"name": "Main account", "market": "crypto", "venue": "sim", "symbols": SYMBOLS})
    engine.create_team(acc.id, SYMBOLS, 400, ["trend_rider", "breakout"], "balanced")
    engine.start_council()
    await engine._tick_account(acc)
    return engine, acc
