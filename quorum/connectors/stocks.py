"""US stocks and ETFs through Alpaca (paper and live accounts)."""
from __future__ import annotations

import asyncio
from datetime import datetime

import httpx

from ..models import Candle, Market, Side
from .base import Connector, ExecResult, KeyCheck

TF = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day"}


class AlpacaConnector(Connector):
    market = Market.STOCKS
    venue = "alpaca"

    def __init__(self, api_key: str, secret: str, sandbox: bool = True, timeframe: str = "5m") -> None:
        self.timeframe = timeframe
        base = "https://paper-api.alpaca.markets" if sandbox else "https://api.alpaca.markets"
        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret}
        self.trade = httpx.AsyncClient(base_url=base, headers=headers, timeout=15)
        self.data = httpx.AsyncClient(base_url="https://data.alpaca.markets", headers=headers, timeout=15)

    async def candles(self, symbol: str, limit: int = 200) -> list[Candle]:
        r = await self.data.get(f"/v2/stocks/{symbol}/bars",
                                params={"timeframe": TF.get(self.timeframe, "5Min"), "limit": limit,
                                        "feed": "iex", "sort": "desc"})
        r.raise_for_status()
        bars = list(reversed(r.json().get("bars") or []))
        out = []
        for b in bars:
            ts = int(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000)
            out.append(Candle(ts, b["o"], b["h"], b["l"], b["c"], b["v"]))
        return out

    async def check(self) -> KeyCheck:
        try:
            r = await self.trade.get("/v2/account")
        except Exception as exc:
            return KeyCheck(False, False, None, f"Cannot reach Alpaca: {exc}")
        if r.status_code != 200:
            return KeyCheck(False, False, None, f"Alpaca rejected the key (HTTP {r.status_code}).")
        acc = r.json()
        if acc.get("trading_blocked"):
            return KeyCheck(False, False, False, "Trading is blocked on this account.")
        # Alpaca trading keys cannot move money out of the account.
        return KeyCheck(True, True, False, "Key works. Alpaca trading keys cannot withdraw funds.",
                        float(acc.get("cash") or 0))

    async def market_order(self, symbol: str, side: Side, qty: float) -> ExecResult:
        qty = round(qty, 6)
        r = await self.trade.post("/v2/orders", json={
            "symbol": symbol, "qty": str(qty), "side": side.value,
            "type": "market", "time_in_force": "day"})
        r.raise_for_status()
        oid = r.json()["id"]
        for _ in range(10):
            await asyncio.sleep(0.5)
            o = (await self.trade.get(f"/v2/orders/{oid}")).json()
            if o.get("filled_avg_price"):
                return ExecResult(float(o["filled_avg_price"]), float(o["filled_qty"]), 0.0)
        bars = await self.candles(symbol, 1)
        return ExecResult(bars[-1].close if bars else 0.0, qty, 0.0)

    async def close(self) -> None:
        await self.trade.aclose()
        await self.data.aclose()
