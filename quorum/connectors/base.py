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
    fee: float              # total fee in the quote currency
    fee_base: float = 0.0   # part of the fee taken from the bought asset itself (you receive less of it)


@dataclass
class StopState:
    """Where a resting stop order on the exchange stands."""
    status: str          # "open" | "filled" | "gone" (cancelled, expired or rejected without filling)
    price: float = 0.0   # average fill price when filled
    qty: float = 0.0     # filled quantity
    fee: float = 0.0     # fee in the quote currency


class Connector(ABC):
    market: Market
    venue: str
    timeframe: str = "5m"
    poll_seconds: float = 15.0
    partial_last_bar: bool = True  # venue returns the still-forming candle last
    supports_stops: bool = False   # can keep a stop order resting on the exchange

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

    # Stops that live on the exchange, so a position stays protected while this computer is off.

    async def place_stop(self, symbol: str, side: Side, qty: float, trigger: float) -> str:
        """Rest a stop order that sells (or buys back) `qty` once price crosses `trigger`. Returns its id."""
        raise NotImplementedError(f"{self.venue} stop orders are not supported")

    async def stop_state(self, symbol: str, order_id: str) -> StopState:
        raise NotImplementedError

    async def cancel_stop(self, symbol: str, order_id: str) -> StopState:
        """Cancel a stop. If it filled first, the returned state says so and carries the fill."""
        raise NotImplementedError

    async def extras(self, symbol: str) -> dict[str, float]:
        """Data beyond candles (funding, open interest, order book). Empty when the venue offers none."""
        return {}

    def min_order_value(self, symbol: str) -> float:
        """Smallest order the venue accepts, in the quote currency. 0 when the venue doesn't say."""
        return 0.0

    async def close(self) -> None:
        return None
