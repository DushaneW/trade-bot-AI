"""Bot contracts.

Analysts read the market and publish signals. Traders read the market
and the council's consensus, then express an intent. Neither talks to
a broker directly: intents go through the risk manager.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np

from ..models import Side


@dataclass
class Series:
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    @property
    def price(self) -> float:
        return float(self.close[-1])

    def __len__(self) -> int:
        return len(self.close)

    def tail(self, n: int) -> "Series":
        """The last n bars (views, not copies)."""
        if n >= len(self.close):
            return self
        return Series(self.high[-n:], self.low[-n:], self.close[-n:], self.volume[-n:])


@dataclass
class Reading:
    """What an analyst concluded about one instrument."""
    kind: str
    direction: float   # -1 bearish .. +1 bullish (for kind="risk": -1 = risk-off)
    confidence: float  # 0..1
    note: str


@dataclass
class Consensus:
    direction: float = 0.0   # reputation-weighted, -1..1
    risk_off: float = 0.0    # 0 calm .. 1 everyone is warning
    voices: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Intent:
    side: Side
    size: float        # fraction of the bot's equity to commit (entries); ignored for exits
    reason: str
    exit: bool = False


@dataclass
class PositionView:
    qty: float
    avg_price: float

    @property
    def flat(self) -> bool:
        return abs(self.qty) < 1e-12

    @property
    def long(self) -> bool:
        return self.qty > 1e-12


class Strategy(ABC):
    key: ClassVar[str]
    title: ClassVar[str]
    persona: ClassVar[str]         # default name the bot introduces itself with
    role: ClassVar[str]            # "analyst" | "trader"
    summary: ClassVar[str]
    defaults: ClassVar[dict[str, float]] = {}
    min_bars: ClassVar[int] = 60
    lookback: ClassVar[int] = 220  # bars the strategy is shown; multi-timeframe analysts ask for more

    def __init__(self, params: dict[str, float] | None = None) -> None:
        self.p = {**self.defaults, **(params or {})}


class Analyst(Strategy):
    role = "analyst"

    @abstractmethod
    def read(self, s: Series) -> Reading | None: ...

    def read_all(self, series: dict[str, Series]) -> dict[str, Reading]:
        """Cross-market analysts override this; by default read one by one."""
        out = {}
        for key, s in series.items():
            if len(s) >= self.min_bars:
                r = self.read(s)
                if r:
                    out[key] = r
        return out


class Trader(Strategy):
    role = "trader"

    @abstractmethod
    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None: ...
