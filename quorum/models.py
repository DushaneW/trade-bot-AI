"""Core data types shared across the engine."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Market(str, Enum):
    CRYPTO = "crypto"
    STOCKS = "stocks"
    FOREX = "forex"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Mode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class Instrument:
    """A tradable symbol on a specific account/venue."""
    account_id: str
    market: Market
    symbol: str  # e.g. "BTC/USDT", "AAPL", "EUR_USD"

    @property
    def key(self) -> str:
        return f"{self.account_id}:{self.symbol}"


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    """A message an analyst publishes on the bus.

    direction: -1 (bearish) .. +1 (bullish). confidence: 0..1.
    kind: what the analyst measures ("trend", "volatility", "risk", ...).
    """
    source: str
    instrument_key: str
    kind: str
    direction: float
    confidence: float
    note: str
    price: float
    ts: int = field(default_factory=now_ms)
    id: str = field(default_factory=lambda: new_id("sig"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Order:
    bot_id: str
    instrument_key: str
    side: Side
    qty: float
    reason: str
    mode: Mode
    id: str = field(default_factory=lambda: new_id("ord"))
    ts: int = field(default_factory=now_ms)


@dataclass
class Fill:
    order_id: str
    bot_id: str
    instrument_key: str
    side: Side
    qty: float
    price: float
    fee: float
    mode: Mode
    reason: str
    pnl: float = 0.0  # realised PnL on closing fills
    ts: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["mode"] = self.mode.value
        return d
