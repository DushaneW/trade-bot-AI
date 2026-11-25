"""Broker connector interface.

One connector per user account. Every market (crypto, stocks, forex)
implements the same small surface so bots never care where they trade.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import Candle, Market, Side


@dataclass
class KeyCheck:
    ok: bool
    can_trade: bool
    can_withdraw: bool | None  # None = venue does not report it
    message: str
    balance_quote: float | None = None


@dataclass
class ExecResult:
    price: float
    qty: float
    fee: float


class Connector(ABC):
    market: Market
    venue: str
    timeframe: str = "5m"
    poll_seconds: float = 15.0
    partial_last_bar: bool = True  # venue returns the still-forming candle last

    @abstractmethod
    async def candles(self, symbol: str, limit: int = 200) -> list[Candle]: ...

    async def history(self, symbol: str, bars: int) -> list[Candle]:
        """Closed candles for backtesting, oldest first. Venues with deeper history override this."""
        candles = await self.candles(symbol, min(bars + 1, 1000))
        return candles[:-1] if self.partial_last_bar else candles

    @abstractmethod
    async def check(self) -> KeyCheck: ...

    @abstractmethod
    async def market_order(self, symbol: str, side: Side, qty: float) -> ExecResult: ...

    def normalize_qty(self, symbol: str, qty: float) -> float:
        return qty

    async def close(self) -> None:
        return None
