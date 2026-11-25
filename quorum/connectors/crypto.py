"""Crypto exchanges through ccxt (Binance, Bybit, Kraken, OKX, Coinbase, ...)."""
from __future__ import annotations

import re

import ccxt as ccxt_sync
import ccxt.async_support as ccxt

from ..models import Candle, Market, Side
from .base import Connector, ExecResult, KeyCheck


def _reason(exc: Exception) -> str:
    """ccxt errors read 'binance {"code":-2008,"msg":"Invalid Api-Key ID."}'; keep the human part."""
    text = str(exc)
    match = re.search(r'"(?:msg|message|retMsg|error)"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else text.split(" ", 1)[-1][:200]


def has_testnet(venue: str) -> bool:
    try:
        return "test" in getattr(ccxt_sync, venue)().urls  # sync class: no network session is opened
    except Exception:
        return False


class CryptoConnector(Connector):
    market = Market.CRYPTO
    poll_seconds = 5.0

    def __init__(self, venue: str, api_key: str = "", secret: str = "",
                 password: str = "", sandbox: bool = True, timeframe: str = "1m") -> None:
        if venue not in ccxt.exchanges:
            raise ValueError(f"Unknown exchange: {venue}")
        self.venue = venue
        self.timeframe = timeframe
        self.has_keys = bool(api_key and secret)
        cfg = {"enableRateLimit": True, "apiKey": api_key, "secret": secret}
        if password:
            cfg["password"] = password
        self.ex = getattr(ccxt, venue)(cfg)
        self.sandbox = sandbox
        if sandbox:
            if not has_testnet(venue):
                # Never fall back to live silently: the owner asked for a testnet.
                raise ValueError(f"{venue.title()} has no testnet. Untick testnet to use a live account.")
            self.ex.set_sandbox_mode(True)
        self._markets_loaded = False

    async def _markets(self) -> None:
        if not self._markets_loaded:
            await self.ex.load_markets()
            self._markets_loaded = True

    async def candles(self, symbol: str, limit: int = 200) -> list[Candle]:
        rows = await self.ex.fetch_ohlcv(symbol, self.timeframe, limit=limit)
        return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)) for r in rows]

    async def history(self, symbol: str, bars: int) -> list[Candle]:
        """Page forward from a start time, so histories longer than one request are possible."""
        step_ms = self.ex.parse_timeframe(self.timeframe) * 1000
        since = self.ex.milliseconds() - (bars + 1) * step_ms
        out: dict[int, Candle] = {}
        while len(out) < bars + 1:
            rows = await self.ex.fetch_ohlcv(symbol, self.timeframe, since=since, limit=1000)
            if not rows:
                break
            for r in rows:
                out[int(r[0])] = Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0))
            if rows[-1][0] <= since:
                break
            since = int(rows[-1][0]) + step_ms
        candles = [out[k] for k in sorted(out)]
        return candles[:-1][-bars:]  # drop the still-forming bar

    async def check(self) -> KeyCheck:
        try:
            await self._markets()
            if not self.has_keys:
                return KeyCheck(True, False, False, "Public market data only. Add an API key to trade.")
            bal = await self.ex.fetch_balance()
        except (ccxt.AuthenticationError, ccxt.PermissionDenied) as exc:
            return KeyCheck(False, False, None, _reason(exc))
        except ccxt.NetworkError as exc:
            return KeyCheck(False, False, None, f"Cannot reach the exchange: {_reason(exc)}")
        except Exception as exc:
            return KeyCheck(False, False, None, _reason(exc))
        quote = 0.0
        for cur in ("USDT", "USDC", "USD", "EUR"):
            quote += float((bal.get("free") or {}).get(cur) or 0)

        can_withdraw: bool | None = None
        if self.venue in ("binance", "binanceusdm"):
            try:
                r = await self.ex.sapi_get_account_apirestrictions()
                can_withdraw = bool(r.get("enableWithdrawals"))
            except Exception:
                can_withdraw = None
        if can_withdraw:
            return KeyCheck(False, True, True,
                            "This key can withdraw funds. Create a trade-only key; Quorum refuses to use this one.",
                            quote)
        if can_withdraw is False:
            note = "Key works. Withdrawals are disabled."
        else:
            note = "Key works. This exchange does not report key permissions; make sure withdrawals are off."
        return KeyCheck(True, True, can_withdraw, note, quote)

    def normalize_qty(self, symbol: str, qty: float) -> float:
        try:
            return float(self.ex.amount_to_precision(symbol, qty))
        except Exception:
            return qty

    async def market_order(self, symbol: str, side: Side, qty: float) -> ExecResult:
        await self._markets()
        qty = self.normalize_qty(symbol, qty)
        o = await self.ex.create_order(symbol, "market", side.value, qty)
        price = float(o.get("average") or o.get("price") or 0)
        if not price:
            t = await self.ex.fetch_ticker(symbol)
            price = float(t["last"])
        fee = float((o.get("fee") or {}).get("cost") or qty * price * 0.001)
        return ExecResult(price, float(o.get("filled") or qty), fee)

    async def close(self) -> None:
        await self.ex.close()
