"""Crypto exchanges through ccxt (Binance, Bybit, Kraken, OKX, Coinbase, ...)."""
from __future__ import annotations

import re

import ccxt as ccxt_sync
import ccxt.async_support as ccxt

from ..models import Candle, Market, Side
from .base import Connector, ExecResult, KeyCheck, StopState

# Venues that keep trigger orders in a separate book and need a flag to look them up or cancel them.
TRIGGER_PARAMS = {"okx": {"trigger": True}, "bybit": {"trigger": True}, "bitget": {"trigger": True},
                  "kucoin": {"stop": True}, "gate": {"trigger": True}, "mexc": {"trigger": True}}
STOP_LIMIT_ROOM = 0.01  # a stop-limit may fill up to 1% past its trigger, so a fast fall still gets out
BOOK_DEPTH = 0.005      # order-book imbalance counts resting size within 0.5% of the mid price


def _reason(exc: Exception) -> str:
    """ccxt errors read 'binance {"code":-2008,"msg":"Invalid Api-Key ID."}'; keep the human part."""
    text = str(exc)
    match = re.search(r'"(?:msg|message|retMsg|error)"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else text.split(" ", 1)[-1][:200]


def has_testnet(venue: str) -> bool:
    try:
        # ccxt lists a "test" entry for every exchange, empty for those without a testnet.
        return bool(getattr(ccxt_sync, venue)().urls.get("test"))  # sync class: no network session is opened
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

    @property
    def supports_stops(self) -> bool:
        has = self.ex.has
        return self.has_keys and bool(has.get("createStopMarketOrder") or has.get("createStopLimitOrder"))

    def min_order_value(self, symbol: str) -> float:
        market = (self.ex.markets or {}).get(symbol) or {}
        return float(((market.get("limits") or {}).get("cost") or {}).get("min") or 0)

    async def market_order(self, symbol: str, side: Side, qty: float) -> ExecResult:
        await self._markets()
        market = self.ex.market(symbol)
        base, quote = market["base"], market["quote"]
        if side == Side.SELL:
            # Never try to sell more than the account holds: fees taken in the base asset or rounding
            # can leave slightly less than the books expect, and the exchange would reject the whole order.
            free = float(((await self.ex.fetch_balance()).get("free") or {}).get(base) or 0)
            qty = min(qty, free)
        qty = self.normalize_qty(symbol, qty)
        if qty <= 0:
            raise RuntimeError(f"Nothing to {side.value}: the account holds no free {base}")

        o = await self.ex.create_order(symbol, "market", side.value, qty)
        if not o.get("filled") and o.get("id"):
            try:
                o = await self.ex.fetch_order(o["id"], symbol)
            except Exception:
                pass  # some venues cannot look up a market order; fall back to what we have
        price = float(o.get("average") or o.get("price") or 0)
        if not price:
            price = float((await self.ex.fetch_ticker(symbol))["last"])
        filled = float(o.get("filled") or qty)
        fee_quote, fee_base = _fees(o, base, quote, price, filled)
        return ExecResult(price, filled, fee_quote, fee_base)

    # ------------------------------------------------------------------ stops on the exchange

    async def place_stop(self, symbol: str, side: Side, qty: float, trigger: float) -> str:
        await self._markets()
        qty = self.normalize_qty(symbol, qty)
        if qty <= 0:
            raise RuntimeError("Position is too small for a stop order")
        trig = float(self.ex.price_to_precision(symbol, trigger))
        if self.ex.has.get("createStopMarketOrder"):
            o = await self.ex.create_stop_market_order(symbol, side.value, qty, trig)
        else:
            # Binance spot, Coinbase and Gate have no stop-market order: use a stop-limit with room to fill.
            room = 1 - STOP_LIMIT_ROOM if side == Side.SELL else 1 + STOP_LIMIT_ROOM
            limit = float(self.ex.price_to_precision(symbol, trigger * room))
            o = await self.ex.create_stop_limit_order(symbol, side.value, qty, limit, trig)
        return str(o["id"])

    async def _order(self, order_id: str, symbol: str) -> dict:
        extra = TRIGGER_PARAMS.get(self.venue)
        if extra:
            try:
                return await self.ex.fetch_order(order_id, symbol, extra)
            except ccxt.OrderNotFound:
                pass  # some of these venues move a triggered stop into the regular book
        return await self.ex.fetch_order(order_id, symbol)

    async def stop_state(self, symbol: str, order_id: str) -> StopState:
        await self._markets()
        try:
            o = await self._order(order_id, symbol)
        except ccxt.OrderNotFound:
            return StopState("gone")
        filled = float(o.get("filled") or 0)
        status = o.get("status")
        if status == "open":
            return StopState("open")  # waiting, or partly filled and still working: settle it once it finishes
        if filled > 0:
            market = self.ex.market(symbol)
            price = float(o.get("average") or o.get("price") or 0)
            fee_quote, _ = _fees(o, market["base"], market["quote"], price, filled)
            return StopState("filled", price, filled, fee_quote)
        return StopState("gone")

    async def cancel_stop(self, symbol: str, order_id: str) -> StopState:
        await self._markets()
        try:
            await self.ex.cancel_order(order_id, symbol, TRIGGER_PARAMS.get(self.venue, {}))
        except ccxt.OrderNotFound:
            pass  # already filled or gone: the state below tells which
        except Exception:
            state = await self.stop_state(symbol, order_id)
            if state.status == "open":
                raise
            return state
        try:
            return await self.stop_state(symbol, order_id)
        except Exception:
            return StopState("gone")  # some venues cannot look up a cancelled order

    # ------------------------------------------------------------------ data beyond candles

    def _perp(self, symbol: str) -> str | None:
        market = (self.ex.markets or {}).get(symbol)
        if not market:
            return None
        perp = f"{market['base']}/{market['quote']}:{market['quote']}"
        return perp if perp in self.ex.markets else None

    async def extras(self, symbol: str) -> dict[str, float]:
        await self._markets()
        out: dict[str, float] = {}
        try:
            book = await self.ex.fetch_order_book(symbol, 50)
            if book["bids"] and book["asks"]:
                mid = (book["bids"][0][0] + book["asks"][0][0]) / 2
                bid = sum(level[1] for level in book["bids"] if level[0] >= mid * (1 - BOOK_DEPTH))
                ask = sum(level[1] for level in book["asks"] if level[0] <= mid * (1 + BOOK_DEPTH))
                if bid + ask > 0:
                    out["book_imbalance"] = (bid - ask) / (bid + ask)
        except Exception:
            pass  # extras are optional: the analysts that need them stay quiet
        perp = self._perp(symbol)
        if perp and self.ex.has.get("fetchFundingRate"):
            try:
                rate = (await self.ex.fetch_funding_rate(perp)).get("fundingRate")
                if rate is not None:
                    out["funding_rate"] = float(rate)
            except Exception:
                pass
        if perp and self.ex.has.get("fetchOpenInterestHistory"):
            try:
                rows = await self.ex.fetch_open_interest_history(perp, "5m", limit=13)
                amounts = [float(r.get("openInterestAmount") or r.get("openInterestValue") or 0) for r in rows]
                if len(amounts) >= 2 and amounts[0] > 0:
                    out["oi_change"] = amounts[-1] / amounts[0] - 1
                    out["oi_minutes"] = 5.0 * (len(amounts) - 1)
            except Exception:
                pass
        return out

    async def close(self) -> None:
        await self.ex.close()


def _fees(o: dict, base: str, quote: str, price: float, filled: float) -> tuple[float, float]:
    """(fee in quote, part of it taken from the bought coin) for a ccxt order."""
    fee_quote, fee_base, known = 0.0, 0.0, False
    for f in (o.get("fees") or ([o["fee"]] if o.get("fee") else [])):
        cost, currency = float(f.get("cost") or 0), f.get("currency")
        if not cost:
            continue
        known = True
        if currency == base:
            fee_base += cost
            fee_quote += cost * price
        elif currency == quote:
            fee_quote += cost
        else:  # paid in another token (e.g. BNB): count it at the usual taker rate
            fee_quote += filled * price * 0.001
    if not known:
        fee_quote = filled * price * 0.001
    return fee_quote, fee_base
