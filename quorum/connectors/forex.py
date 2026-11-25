"""Forex through OANDA v20 (practice and live accounts)."""
from __future__ import annotations

from datetime import datetime

import httpx

from ..models import Candle, Market, Side
from .base import Connector, ExecResult, KeyCheck, StopState

TF = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "1d": "D"}


class OandaConnector(Connector):
    market = Market.FOREX
    venue = "oanda"
    supports_stops = True

    def __init__(self, token: str, account_id: str, sandbox: bool = True, timeframe: str = "5m") -> None:
        self.timeframe = timeframe
        self.account_id = account_id
        base = "https://api-fxpractice.oanda.com" if sandbox else "https://api-fxtrade.oanda.com"
        self.http = httpx.AsyncClient(base_url=base, timeout=15,
                                      headers={"Authorization": f"Bearer {token}"})

    async def candles(self, symbol: str, limit: int = 200) -> list[Candle]:
        r = await self.http.get(f"/v3/instruments/{symbol}/candles",
                                params={"granularity": TF.get(self.timeframe, "M5"), "count": limit, "price": "M"})
        r.raise_for_status()
        out = []
        for c in r.json().get("candles", []):
            m = c["mid"]
            ts = int(datetime.fromisoformat(c["time"][:26].rstrip("Z") + "+00:00").timestamp() * 1000)
            out.append(Candle(ts, float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]), float(c.get("volume", 0))))
        return out

    async def check(self) -> KeyCheck:
        try:
            r = await self.http.get(f"/v3/accounts/{self.account_id}/summary")
        except Exception as exc:
            return KeyCheck(False, False, None, f"Cannot reach OANDA: {exc}")
        if r.status_code != 200:
            return KeyCheck(False, False, None, f"OANDA rejected the token or account ID (HTTP {r.status_code}).")
        bal = float(r.json()["account"].get("balance") or 0)
        return KeyCheck(True, True, False, "Token works. The OANDA API cannot withdraw funds.", bal)

    def normalize_qty(self, symbol: str, qty: float) -> float:
        return float(int(qty))

    async def market_order(self, symbol: str, side: Side, qty: float) -> ExecResult:
        units = int(qty) if side == Side.BUY else -int(qty)
        r = await self.http.post(f"/v3/accounts/{self.account_id}/orders", json={
            "order": {"units": str(units), "instrument": symbol, "type": "MARKET", "timeInForce": "FOK"}})
        r.raise_for_status()
        fill = r.json().get("orderFillTransaction")
        if not fill:
            reason = r.json().get("orderCancelTransaction", {}).get("reason", "unknown")
            raise RuntimeError(f"OANDA did not fill the order: {reason}")
        return ExecResult(float(fill["price"]), abs(float(fill["units"])), abs(float(fill.get("financing", 0))))

    async def place_stop(self, symbol: str, side: Side, qty: float, trigger: float) -> str:
        units = int(qty) if side == Side.BUY else -int(qty)
        digits = 3 if "JPY" in symbol else 5
        r = await self.http.post(f"/v3/accounts/{self.account_id}/orders", json={"order": {
            "type": "STOP", "instrument": symbol, "units": str(units), "price": f"{trigger:.{digits}f}",
            "timeInForce": "GTC", "positionFill": "REDUCE_ONLY"}})
        body = r.json()
        if r.status_code >= 400 or "orderCreateTransaction" not in body:
            reason = body.get("errorMessage") or body.get("orderRejectTransaction", {}).get("rejectReason")
            raise RuntimeError(f"OANDA refused the stop: {reason or r.status_code}")
        return str(body["orderCreateTransaction"]["id"])

    async def stop_state(self, symbol: str, order_id: str) -> StopState:
        r = await self.http.get(f"/v3/accounts/{self.account_id}/orders/{order_id}")
        if r.status_code == 404:
            return StopState("gone")
        r.raise_for_status()
        o = r.json()["order"]
        if o["state"] in ("PENDING", "TRIGGERED"):
            return StopState("open")
        if o["state"] == "FILLED" and o.get("fillingTransactionID"):
            t = (await self.http.get(f"/v3/accounts/{self.account_id}/transactions/{o['fillingTransactionID']}")).json()
            tx = t.get("transaction", {})
            return StopState("filled", float(tx.get("price") or 0), abs(float(tx.get("units") or 0)),
                             abs(float(tx.get("financing") or 0)))
        return StopState("gone")

    async def cancel_stop(self, symbol: str, order_id: str) -> StopState:
        r = await self.http.put(f"/v3/accounts/{self.account_id}/orders/{order_id}/cancel")
        if r.status_code == 200:
            return StopState("gone")
        return await self.stop_state(symbol, order_id)  # could not cancel: it filled or was already gone

    async def close(self) -> None:
        await self.http.aclose()
