"""Simulated market so Quorum runs out of the box, without any account.

Prices follow a random walk that switches between calm, trending and
turbulent regimes, which gives every strategy something to react to.
A new candle forms every `bar_seconds` of real time.
"""
from __future__ import annotations

import itertools
import math
import random
import time

from ..models import Candle, Market, Side
from .base import Connector, ExecResult, KeyCheck, StopState

DEFAULTS = {
    "SIM-BTC": 64000.0,
    "SIM-ETH": 3200.0,
    "SIM-SOL": 150.0,
    "SIM-ACME": 210.0,
    "SIM-EURUSD": 1.085,
    # Common pair names so screenshots and tests read naturally; prices are still simulated.
    "BTC/USDT": 64000.0,
    "ETH/USDT": 3200.0,
    "SOL/USDT": 150.0,
}


class _Path:
    def __init__(self, symbol: str, start: float, bar_seconds: float,
                 bars: int = 400, seed: str | None = None, keep: int = 1000) -> None:
        self.rng = random.Random(seed or symbol)
        self.bar = bar_seconds
        self.keep = keep
        self.price = start
        self.regime_left = 0
        self.drift = 0.0
        self.vol = 0.002
        self.candles: list[Candle] = []
        # Snap to a shared grid so every simulated market prints candles at the same timestamps.
        t0 = (time.time() // bar_seconds) * bar_seconds - bars * bar_seconds
        for i in range(bars):
            self._append(int((t0 + i * bar_seconds) * 1000))
        self.last_t = t0 + bars * bar_seconds

    def _regime(self) -> None:
        roll = self.rng.random()
        if roll < 0.4:
            self.drift, self.vol = 0.0, 0.0015          # calm range
        elif roll < 0.8:
            self.drift = self.rng.choice([-1, 1]) * 0.0008
            self.vol = 0.002                             # trend
        else:
            self.drift, self.vol = 0.0, 0.006            # turbulence
        self.regime_left = self.rng.randint(30, 120)

    def _append(self, ts: int) -> None:
        if self.regime_left <= 0:
            self._regime()
        self.regime_left -= 1
        o = self.price
        steps = [self.rng.gauss(self.drift / 4, self.vol / 2) for _ in range(4)]
        path = [o]
        for s in steps:
            path.append(path[-1] * math.exp(s))
        c = path[-1]
        self.price = c
        self.candles.append(Candle(ts, o, max(path), min(path), c, self.rng.uniform(50, 500)))
        if len(self.candles) > self.keep:
            del self.candles[: len(self.candles) - self.keep + 200]

    def advance(self) -> None:
        now = time.time()
        while now - self.last_t >= self.bar:
            self.last_t += self.bar
            self._append(int(self.last_t * 1000))


class SimConnector(Connector):
    venue = "sim"
    timeframe = "sim"
    poll_seconds = 1.0
    partial_last_bar = False
    supports_stops = True

    def __init__(self, market: Market = Market.CRYPTO, bar_seconds: float = 4.0) -> None:
        self.market = market
        self.bar_seconds = bar_seconds
        self.paths: dict[str, _Path] = {}
        self.stops: dict[str, dict] = {}
        self._ids = itertools.count(1)

    def _path(self, symbol: str) -> _Path:
        if symbol not in self.paths:
            self.paths[symbol] = _Path(symbol, DEFAULTS.get(symbol, 100.0), self.bar_seconds)
        p = self.paths[symbol]
        p.advance()
        self._trigger_stops(symbol, p)
        return p

    def _trigger_stops(self, symbol: str, path: _Path) -> None:
        for stop in self.stops.values():
            if stop["symbol"] != symbol or stop["status"] != "open":
                continue
            for c in path.candles:
                if c.ts <= stop["since"]:
                    continue
                crossed = c.low <= stop["trigger"] if stop["side"] == Side.SELL else c.high >= stop["trigger"]
                if crossed:
                    # A gap through the trigger fills at the open, like a real stop-market order.
                    px = min(stop["trigger"], c.open) if stop["side"] == Side.SELL else max(stop["trigger"], c.open)
                    stop.update(status="filled", price=px, fee=stop["qty"] * px * 0.001)
                    break

    async def place_stop(self, symbol: str, side: Side, qty: float, trigger: float) -> str:
        path = self._path(symbol)
        oid = f"sim-stop-{next(self._ids)}"
        self.stops[oid] = {"symbol": symbol, "side": side, "qty": qty, "trigger": trigger,
                           "since": path.candles[-1].ts, "status": "open"}
        return oid

    async def stop_state(self, symbol: str, order_id: str) -> StopState:
        self._path(symbol)
        stop = self.stops.get(order_id)
        if stop is None or stop["status"] == "gone":
            return StopState("gone")
        if stop["status"] == "filled":
            return StopState("filled", stop["price"], stop["qty"], stop["fee"])
        return StopState("open")

    async def cancel_stop(self, symbol: str, order_id: str) -> StopState:
        state = await self.stop_state(symbol, order_id)
        if state.status == "open":
            self.stops[order_id]["status"] = "gone"
            return StopState("gone")
        return state

    async def extras(self, symbol: str) -> dict[str, float]:
        """Plausible funding, open interest and order-book readings derived from the simulated path."""
        path = self._path(symbol)
        closes = [c.close for c in path.candles[-60:]]
        drift = closes[-1] / closes[0] - 1 if len(closes) > 1 else 0.0
        rng = random.Random(f"{symbol}:{path.candles[-1].ts}")
        return {
            "funding_rate": 0.0001 + drift * 0.01 + rng.gauss(0, 0.00008),
            "oi_change": drift * rng.uniform(-1.5, 2.5) + rng.gauss(0, 0.01),
            "oi_minutes": 60.0,
            "book_imbalance": max(-1.0, min(1.0, rng.gauss(drift * 8, 0.25))),
        }

    async def candles(self, symbol: str, limit: int = 200) -> list[Candle]:
        return self._path(symbol).candles[-limit:]

    async def history(self, symbol: str, bars: int) -> list[Candle]:
        # A fresh, longer path from the same generator, seeded by length so a run is repeatable.
        # Historical bars are spaced one minute apart so time-based metrics (Sharpe, daily limits)
        # read like a real 1m chart rather than the 4-second live clock of the simulator.
        path = _Path(symbol, DEFAULTS.get(symbol, 100.0), 60.0, bars=bars,
                     seed=f"{symbol}:history:{bars}", keep=bars)
        return path.candles

    async def check(self) -> KeyCheck:
        return KeyCheck(True, True, False, "Simulated market. No real money involved.")

    async def market_order(self, symbol: str, side: Side, qty: float) -> ExecResult:
        price = self._path(symbol).price
        return ExecResult(price, qty, abs(qty * price) * 0.001)
