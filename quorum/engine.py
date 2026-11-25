"""The engine: one event loop that feeds markets to bots and orders to brokers.

Per account, on every new closed bar:
  1. analysts read the market and publish signals on the bus,
  2. the council scores old predictions and rebuilds consensus,
  3. traders decide; their intents pass the risk manager; fills are booked.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from . import indicators as ta
from .bots import ANALYSTS, STRATEGIES, TRADERS, Analyst, PositionView, Series, Trader
from .bus import Bus
from .connectors import Connector, KeyCheck, build
from .learning import Council, Reputation, regime_of
from .ledger import Ledger
from .models import Fill, Market, Mode, Side, Signal, new_id, now_ms
from .risk import PROFILES, RiskLimits, RiskManager, conviction
from .security import Vault, mask
from .storage import Store

log = logging.getLogger(__name__)

EQUITY_EVERY_S = 10
HISTORY_BARS = 760   # enough for multi-timeframe analysts
WINDOW_BARS = 220    # what most strategies look at
PAPER_REMOVED = ("Paper trading was removed in this version. This trader now places real orders, so it was "
                 "paused. Check its budget, then press Resume.")


class ConfigError(ValueError):
    """Raised for user-correctable configuration problems; surfaced as HTTP 400."""


@dataclass
class AccountRuntime:
    record: dict[str, Any]
    connector: Connector
    status: KeyCheck | None = None
    last_poll: float = 0.0
    error: str | None = None

    @property
    def id(self) -> str:
        return self.record["id"]

    @property
    def market(self) -> Market:
        return Market(self.record["market"])

    def public(self) -> dict:
        r = self.record
        return {
            "id": r["id"], "name": r["name"], "market": r["market"], "venue": r["venue"],
            "sandbox": r["sandbox"], "symbols": r["symbols"], "key_hint": r.get("key_hint", ""),
            "status": None if not self.status else {
                "ok": self.status.ok, "can_trade": self.status.can_trade,
                "can_withdraw": self.status.can_withdraw, "message": self.status.message,
                "balance": self.status.balance_quote,
            },
            "error": self.error,
        }


@dataclass
class BotRuntime:
    config: dict[str, Any]
    strategy: Analyst | Trader
    ledger: Ledger | None = None
    limits: RiskLimits = field(default_factory=RiskLimits)
    paused_reason: str | None = None
    paused_kind: str | None = None
    day: str = ""
    day_start: float = 0.0
    last_action: str = ""

    @property
    def id(self) -> str:
        return self.config["id"]

    @property
    def is_trader(self) -> bool:
        return self.strategy.role == "trader"

    @property
    def keys(self) -> list[str]:
        return [f"{self.config['account_id']}:{s}" for s in self.config["symbols"]]

    @property
    def mode(self) -> Mode:
        return Mode.LIVE  # paper trading was removed; practise on a testnet account instead

    @property
    def size_mult(self) -> float:
        return float(self.config.get("size_mult", 1.0))

    @property
    def active(self) -> bool:
        return self.config.get("enabled", True) and self.paused_reason is None


class Engine:
    def __init__(self, store: Store, vault: Vault, bus: Bus) -> None:
        self.store, self.vault, self.bus = store, vault, bus
        self.risk = RiskManager()
        self.council = Council()
        self.accounts: dict[str, AccountRuntime] = {}
        self.bots: dict[str, BotRuntime] = {}
        self.series: dict[str, Series] = {}
        self.prices: dict[str, float] = {}
        self.bar_ms: dict[str, int] = {}
        self.last_bar: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self._last_equity = 0.0
        self.running = False  # the owner's go signal; until then nothing analyses or trades
        self.regime: dict[str, str] = {}  # key -> "trend" | "chop", refreshed every bar
        self.last_said: dict[str, dict[str, Any]] = {}  # bot id -> latest thing it said or did
        bus.subscribe("*", self._remember)

    def _remember(self, msg: dict) -> None:
        d, topic = msg["data"], msg["topic"]
        if topic == "signal":
            bot, text = d["source"], f"{d['instrument_key'].split(':', 1)[1]}: {d['note']}"
        elif topic == "fill":
            verb = "Bought" if d["side"] == "buy" else "Sold"
            bot, text = d["bot_id"], f"{verb} {d['instrument_key'].split(':', 1)[1]} at {d['price']:.6g}. {d['reason']}"
        elif topic == "rejected":
            bot, text = d["bot"], f"Order refused: {d['reason']}"
        elif topic == "bot" and d.get("event") == "paused":
            bot, text = d["bot"], f"Paused: {d['reason']}"
        else:
            return
        self.last_said[bot] = {"ts": msg["ts"], "text": text, "topic": topic}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Load saved state. Nothing trades until the owner starts the council."""
        for rec in self.store.accounts():
            await self._mount_account(rec)
        for aid, data in self.store.reputations().items():
            self.council.reputation[aid] = Reputation.from_dict(data)
        for cfg in self.store.bots():
            self._mount_bot(cfg)
        self.running = bool(self.store.setting("council_running", False)) and bool(self.bots)
        self._task = asyncio.create_task(self._run(), name="quorum-engine")

    def start_council(self) -> None:
        if not any(b.is_trader for b in self.bots.values()):
            raise ConfigError("There is no team yet. Finish the setup with the Chair first")
        self.running = True
        self.risk.halted = False
        self.store.set_setting("council_running", True)
        self.bus.publish("council", {"running": True})

    async def stop_council(self, close_positions: bool) -> None:
        """Stop analysis and new trades. Open positions keep their stop losses unless closed here."""
        if close_positions:
            for bot in list(self.bots.values()):
                await self.close_positions(bot.id, "Council stopped by owner")
        self.running = False
        self.store.set_setting("council_running", False)
        self.bus.publish("council", {"running": False, "closed": close_positions})

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._persist()
        for acc in self.accounts.values():
            await acc.connector.close()

    def create_team(self, account_id: str, symbols: list[str], budget: float,
                    traders: list[str] | None = None, profile: str = "balanced") -> list[BotRuntime]:
        """Assemble the council on one account: every analyst, the chosen traders, budget split evenly."""
        if self.bots:
            raise ConfigError("A team already exists")
        traders = [t for t in (traders or list(TRADERS)) if t in TRADERS]
        if not traders:
            raise ConfigError("Pick at least one trader")
        share = round(budget / len(traders), 2)
        floor = self.min_trader_budget(account_id, symbols)
        if share < floor:
            raise ConfigError(f"{share:g} per trader is too little: each needs at least {floor:g} to place orders "
                              "above the exchange minimum. Pick fewer traders or raise the budget")
        created = [self.create_bot({"strategy": k, "account_id": account_id, "symbols": symbols}) for k in ANALYSTS]
        for key in traders:
            created.append(self.create_bot({"strategy": key, "account_id": account_id, "symbols": symbols,
                                             "budget": share, "profile": profile}))
        return created

    def min_order_value(self, account_id: str, symbol: str) -> float:
        """The larger of our own floor and what the exchange accepts, with a little headroom."""
        acc = self.accounts.get(account_id)
        venue_min = acc.connector.min_order_value(symbol) if acc else 0.0
        return max(RiskLimits().min_order_value, venue_min * 1.1)

    def min_trader_budget(self, account_id: str, symbols: list[str]) -> float:
        """A trader must afford a few minimum orders, otherwise every entry gets refused."""
        worst = max((self.min_order_value(account_id, s) for s in symbols), default=RiskLimits().min_order_value)
        return round(worst * 3, 2)

    # ------------------------------------------------------------------ accounts

    async def _mount_account(self, rec: dict) -> AccountRuntime:
        secrets = self.vault.open(rec.get("secrets", ""))
        acc = AccountRuntime(rec, build(rec, secrets))
        self.accounts[acc.id] = acc
        await self.check_account(acc.id)
        return acc

    def set_account_symbols(self, account_id: str, symbols: list[str]) -> None:
        acc = self.accounts[account_id]
        clean = [s.strip() for s in symbols if s.strip()]
        if not clean:
            raise ConfigError("Pick at least one market")
        acc.record["symbols"] = clean
        self.store.save_account(acc.record)

    async def add_account(self, data: dict, require_ok: bool = False) -> AccountRuntime:
        market = data.get("market")
        if market not in {m.value for m in Market}:
            raise ConfigError("Choose crypto, stocks or forex")
        symbols = [s.strip() for s in data.get("symbols", []) if s.strip()]
        if not symbols:
            raise ConfigError("Add at least one symbol to watch")
        secrets = {k: data[k].strip() for k in ("api_key", "secret", "password", "account_id")
                   if data.get(k)}
        rec = {
            "id": new_id("acc"), "name": data.get("name") or data["venue"].title(),
            "market": market, "venue": data["venue"], "sandbox": bool(data.get("sandbox", True)),
            "secrets": self.vault.seal(secrets) if secrets else "",
            "symbols": symbols, "created": now_ms(),
        }
        try:
            acc = await self._mount_account(rec)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if require_ok and not (acc.status and acc.status.ok):
            # Do not keep a half-connected account around; the owner fixes the key and tries again.
            self.accounts.pop(acc.id, None)
            await acc.connector.close()
            raise ConfigError(acc.status.message if acc.status else "The account did not respond")
        rec["key_hint"] = mask(secrets.get("api_key", ""))
        self.store.save_account(rec)
        self.bus.publish("account", {"event": "added", "account": acc.public()})
        return acc

    async def check_account(self, account_id: str) -> KeyCheck:
        acc = self.accounts[account_id]
        try:
            acc.status = await acc.connector.check()
            acc.error = None if acc.status.ok else acc.status.message
        except Exception as exc:
            log.exception("Account check failed for %s", account_id)
            acc.status = KeyCheck(False, False, None, str(exc))
            acc.error = str(exc)
        return acc.status

    async def remove_account(self, account_id: str) -> None:
        if any(b.config["account_id"] == account_id for b in self.bots.values()):
            raise ConfigError("Delete or move the bots using this account first")
        acc = self.accounts.pop(account_id)
        await acc.connector.close()
        self.store.delete_account(account_id)

    # ------------------------------------------------------------------ bots

    def _mount_bot(self, cfg: dict) -> BotRuntime:
        cls = STRATEGIES[cfg["strategy"]]
        bot = BotRuntime(cfg, cls(cfg.get("params")), limits=RiskLimits.from_dict(cfg.get("limits")))
        if bot.is_trader:
            state = cfg.get("ledger")
            if cfg.get("mode") == "paper":
                # Saved by an older version. Its paper positions never existed on the exchange, so start clean
                # and make the owner look before this trader places its first real order.
                state = None
                cfg["enabled"] = False
                cfg.pop("mode")
                self.last_said[bot.id] = {"ts": now_ms(), "text": PAPER_REMOVED, "topic": "bot"}
            bot.ledger = Ledger.from_state(state) if state else Ledger(float(cfg["budget"]))
        self.bots[bot.id] = bot
        return bot

    def create_bot(self, data: dict) -> BotRuntime:
        strategy = data.get("strategy")
        if strategy not in STRATEGIES:
            raise ConfigError(f"Unknown strategy: {strategy}")
        account_id = data.get("account_id")
        if account_id not in self.accounts:
            raise ConfigError("Pick an account for this bot")
        allowed = set(self.accounts[account_id].record["symbols"])
        symbols = [s for s in data.get("symbols", []) if s in allowed]
        if not symbols:
            raise ConfigError("Pick at least one symbol the account watches")
        cls = STRATEGIES[strategy]
        cfg = {
            "id": new_id("bot"),
            "name": self._unique_name(str(data.get("name") or cls.persona).strip()[:40]),
            "strategy": strategy,
            "account_id": account_id,
            "symbols": symbols,
            "params": {k: float(v) for k, v in (data.get("params") or {}).items() if k in cls.defaults},
            "enabled": True,
            "created": now_ms(),
        }
        if cls.role == "trader":
            self._require_tradable(account_id)
            budget = float(data.get("budget") or 0)
            if budget <= 0:
                raise ConfigError("Give the trader a budget above zero")
            profile = data.get("profile") or "balanced"
            if profile not in PROFILES:
                raise ConfigError(f"Unknown risk profile: {profile}")
            base = PROFILES[profile]["limits"].to_dict()
            cfg["budget"] = budget
            cfg["profile"] = profile
            cfg["size_mult"] = PROFILES[profile]["size"]
            cfg["limits"] = RiskLimits.from_dict({**base, **(data.get("limits") or {})}).to_dict()
        bot = self._mount_bot(cfg)
        self._save_bot(bot)
        self.bus.publish("bot", {"event": "created", "bot": bot.id, "name": cfg["name"]})
        return bot

    def _unique_name(self, name: str) -> str:
        taken = {b.config["name"] for b in self.bots.values()}
        if name not in taken:
            return name
        n = 2
        while f"{name} {n}" in taken:
            n += 1
        return f"{name} {n}"

    def update_bot(self, bot_id: str, data: dict) -> BotRuntime:
        bot = self.bots[bot_id]
        if "name" in data and str(data["name"]).strip():
            bot.config["name"] = str(data["name"]).strip()[:40]
        if "enabled" in data:
            bot.config["enabled"] = bool(data["enabled"])
            if data["enabled"]:
                bot.paused_reason = bot.paused_kind = None
        if "profile" in data and bot.is_trader:
            profile = str(data["profile"])
            if profile not in PROFILES:
                raise ConfigError(f"Unknown risk profile: {profile}")
            bot.limits = PROFILES[profile]["limits"]
            bot.config.update(profile=profile, size_mult=PROFILES[profile]["size"], limits=bot.limits.to_dict())
        if "limits" in data and bot.is_trader:
            bot.limits = RiskLimits.from_dict({**bot.limits.to_dict(), **data["limits"]})
            bot.config["limits"] = bot.limits.to_dict()
        if "budget" in data and bot.is_trader:
            self._set_budget(bot, float(data["budget"]))
        self._save_bot(bot)
        self.bus.publish("bot", {"event": "updated", "bot": bot.id, "name": bot.config["name"]})
        return bot

    def _require_tradable(self, account_id: str) -> None:
        """Traders place real orders, so their account must be verified, able to trade and unable to withdraw."""
        st = self.accounts[account_id].status
        if not st or not st.ok or not st.can_trade:
            raise ConfigError("This account's key isn't verified for trading. Check the connection on the Accounts page")
        if st.can_withdraw:
            raise ConfigError("This account's key can withdraw funds. Create a trade-only key")

    def _set_budget(self, bot: BotRuntime, budget: float) -> None:
        assert bot.ledger is not None
        if budget <= 0:
            raise ConfigError("Budget must be above zero")
        delta = budget - bot.ledger.budget
        if bot.ledger.cash + delta < 0:
            raise ConfigError("Budget cannot go below the money already in open positions")
        bot.ledger.budget = budget
        bot.ledger.cash += delta
        bot.config["budget"] = budget

    def delete_bot(self, bot_id: str) -> None:
        bot = self.bots[bot_id]
        if bot.ledger and any(p.is_open for p in bot.ledger.positions.values()):
            raise ConfigError("This trader still holds positions on the exchange. Close them first")
        del self.bots[bot_id]
        self.store.delete_bot(bot_id)
        self.bus.publish("bot", {"event": "deleted", "bot": bot_id})

    async def close_positions(self, bot_id: str, reason: str = "Closed by user") -> None:
        bot = self.bots[bot_id]
        if not bot.ledger:
            return
        for key, pos in list(bot.ledger.positions.items()):
            if pos.is_open:
                side = Side.SELL if pos.qty > 0 else Side.BUY
                await self._execute(bot, key, side, abs(pos.qty), reason, force=True)

    def _save_bot(self, bot: BotRuntime) -> None:
        if bot.ledger:
            bot.config["ledger"] = bot.ledger.to_state()
        self.store.save_bot(bot.id, bot.config)

    def _persist(self) -> None:
        for bot in self.bots.values():
            self._save_bot(bot)
        for aid, rep in self.council.reputation.items():
            self.store.save_reputation(aid, rep.to_dict())

    # ------------------------------------------------------------------ kill switch

    async def halt(self, on: bool, flatten: bool = False) -> None:
        self.risk.halted = on
        if on and flatten:
            for bot in self.bots.values():
                await self.close_positions(bot.id, "Kill switch")
        self.bus.publish("halt", {"on": on, "flatten": flatten})

    # ------------------------------------------------------------------ main loop

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            for acc in list(self.accounts.values()):
                if started - acc.last_poll >= acc.connector.poll_seconds:
                    acc.last_poll = started
                    try:
                        if self.running:
                            await self._tick_account(acc)
                        else:
                            await self._guard_open_positions(acc)
                        acc.error = None if (acc.status and acc.status.ok) else acc.error
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        acc.error = f"Market data failed: {exc}"
                        log.warning("Tick failed for %s: %s", acc.id, exc)
            if time.time() - self._last_equity >= EQUITY_EVERY_S:
                self._last_equity = time.time()
                self._record_equity()
            await asyncio.sleep(0.5)

    async def _guard_open_positions(self, acc: AccountRuntime) -> None:
        """While the council is stopped nothing analyses or opens trades, but stop losses still hold."""
        held = {key.split(":", 1)[1] for b in self.bots.values() if b.ledger and b.config["account_id"] == acc.id
                for key, pos in b.ledger.positions.items() if pos.is_open}
        for symbol in held:
            candles = await acc.connector.candles(symbol, 2)
            if candles:
                key = f"{acc.id}:{symbol}"
                self.prices[key] = candles[-1].close
                await self._guard_stops(key)

    def _watched(self, acc: AccountRuntime) -> set[str]:
        return {s for b in self.bots.values() if b.config["account_id"] == acc.id for s in b.config["symbols"]}

    async def _tick_account(self, acc: AccountRuntime) -> None:
        fresh: dict[str, Series] = {}
        for symbol in self._watched(acc):
            key = f"{acc.id}:{symbol}"
            candles = await acc.connector.candles(symbol, HISTORY_BARS)
            if len(candles) < 3:
                continue
            self.prices[key] = candles[-1].close
            closed = candles[:-1] if acc.connector.partial_last_bar else candles
            self.bar_ms[key] = closed[-1].ts - closed[-2].ts
            if closed[-1].ts == self.last_bar.get(key):
                await self._guard_stops(key)
                continue
            self.last_bar[key] = closed[-1].ts
            self.series[key] = fresh[key] = Series(*ta.arrays(closed))
            self.regime[key] = regime_of(ta.efficiency_ratio(fresh[key].close, 30))
            self.bus.publish("bar", {"key": key, "close": closed[-1].close, "ts": closed[-1].ts}, keep=False)
        if not fresh:
            return

        now = now_ms()
        for aid, hit in self.council.score(self.prices, now):
            self.bus.publish("score", {"analyst": aid, "hit": hit, "weight": self.council.rep(aid).weight},
                             keep=False)

        analysts = [b for b in self.bots.values()
                    if not b.is_trader and b.active and b.config["account_id"] == acc.id]
        for bot in analysts:
            assert isinstance(bot.strategy, Analyst)
            watched = {k: fresh[k].tail(bot.strategy.lookback) for k in bot.keys if k in fresh}
            if not watched:
                continue
            for key, reading in bot.strategy.read_all(watched).items():
                s = fresh[key].tail(WINDOW_BARS)
                atr_frac = float(ta.atr(s.high, s.low, s.close)[-1] / s.price)
                self.council.hear(bot.id, key, reading, s.price, now, self.bar_ms[key], atr_frac, self.regime.get(key))
                sig = Signal(bot.id, key, reading.kind, reading.direction, reading.confidence,
                             reading.note, s.price)
                self.bus.publish("signal", {**sig.to_dict(), "bot_name": bot.config["name"]})

        traders = [b for b in self.bots.values() if b.is_trader and b.config["account_id"] == acc.id]
        for bot in traders:
            await self._tick_trader(bot, fresh, now)

    async def _guard_stops(self, key: str) -> None:
        """Between bars, still enforce stop losses on the live price."""
        for bot in self.bots.values():
            if bot.ledger and key in bot.keys and bot.ledger.position(key).is_open:
                reason = self.risk.stop_triggered(bot.ledger, bot.limits, key, self.prices[key])
                if reason:
                    pos = bot.ledger.position(key)
                    await self._execute(bot, key, Side.SELL if pos.qty > 0 else Side.BUY,
                                        abs(pos.qty), reason, force=True)

    async def _tick_trader(self, bot: BotRuntime, fresh: dict[str, Series], now: int) -> None:
        assert bot.ledger is not None and isinstance(bot.strategy, Trader)
        if not bot.config.get("enabled", True):
            return
        equity = bot.ledger.equity(self.prices)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if bot.day != today:
            bot.day, bot.day_start = today, equity
            if bot.paused_kind == "daily":
                bot.paused_reason = bot.paused_kind = None  # new day, new allowance
        if bot.paused_reason:
            return
        breach = self.risk.check_bot(bot.ledger, bot.limits, equity, bot.day_start)
        if breach:
            bot.paused_kind, bot.paused_reason = breach
            await self.close_positions(bot.id, bot.paused_reason)
            self.bus.publish("bot", {"event": "paused", "bot": bot.id, "name": bot.config["name"],
                                     "reason": bot.paused_reason})
            return

        for key in bot.keys:
            s = fresh.get(key)
            if s is None or len(s) < bot.strategy.min_bars:
                continue
            s = s.tail(bot.strategy.lookback)
            pos = bot.ledger.position(key)
            stop = self.risk.stop_triggered(bot.ledger, bot.limits, key, s.price)
            if stop:
                await self._execute(bot, key, Side.SELL if pos.qty > 0 else Side.BUY, abs(pos.qty), stop,
                                    force=True)
                continue
            council = self.council.consensus(key, now, self.bar_ms[key], self.regime.get(key))
            intent = bot.strategy.decide(s, PositionView(pos.qty, pos.avg_price), council)
            if intent is None:
                continue
            if intent.exit:
                if pos.is_open:
                    await self._execute(bot, key, intent.side, abs(pos.qty), intent.reason)
                continue
            size = min(max(intent.size, 0) * bot.size_mult * conviction(council.direction), 1)
            qty = equity * size / self.prices[key]
            await self._execute(bot, key, intent.side, qty, intent.reason, council=council)

    async def _execute(self, bot: BotRuntime, key: str, side: Side, qty: float, reason: str,
                       *, force: bool = False, council: Any = None) -> None:
        assert bot.ledger is not None
        acc = self.accounts[bot.config["account_id"]]
        price = self.prices.get(key)
        if not price:
            return
        equity = bot.ledger.equity(self.prices)
        symbol = key.split(":", 1)[1]
        if force:
            verdict_qty, note = qty, None
        else:
            limits = replace(bot.limits, min_order_value=self.min_order_value(acc.id, symbol))
            verdict = self.risk.review(bot.ledger, limits, acc.market, key, side, qty, price, equity)
            if not verdict.approved:
                self.bus.publish("rejected", {"bot": bot.id, "bot_name": bot.config["name"], "key": key,
                                              "side": side.value, "reason": verdict.reason, "intent": reason})
                return
            verdict_qty, note = verdict.qty, verdict.reason

        order_id = new_id("ord")
        try:
            res = await acc.connector.market_order(symbol, side, acc.connector.normalize_qty(symbol, verdict_qty))
            fill_price, fee = res.price, res.fee
            # A fee taken from the bought coin means we hold less of it than we bought.
            fill_qty = res.qty - res.fee_base if side == Side.BUY else res.qty
        except Exception as exc:
            log.exception("Order failed for bot %s", bot.id)
            self.bus.publish("error", {"bot": bot.id, "bot_name": bot.config["name"], "key": key,
                                       "message": f"Order failed: {exc}"})
            return

        fill = Fill(order_id, bot.id, key, side, fill_qty, fill_price, fee, bot.mode,
                    reason + (f" ({note})" if note else ""))
        fill.pnl = bot.ledger.apply(fill)
        left = bot.ledger.position(key)
        if side == Side.SELL and left.is_open and abs(left.qty) * fill_price < self.min_order_value(acc.id, symbol) / 2:
            # A crumb too small to ever sell (rounding or fees). Stop tracking it so exits don't retry forever.
            log.info("Writing off %s %s dust for bot %s", left.qty, symbol, bot.id)
            bot.ledger.positions.pop(key, None)
        bot.last_action = fill.reason
        payload = fill.to_dict()
        payload["bot_name"] = bot.config["name"]
        if council is not None:
            payload["council"] = {"direction": council.direction, "risk_off": council.risk_off,
                                  "voices": len(council.voices)}
        self.store.add_fill(payload)
        self._save_bot(bot)
        self.bus.publish("fill", payload)

    def _record_equity(self) -> None:
        ts = now_ms()
        total = 0.0
        for bot in self.bots.values():
            if bot.ledger:
                eq = bot.ledger.equity(self.prices)
                total += eq
                self.store.add_equity(bot.id, ts, eq)
        self.store.add_equity("__all__", ts, total)
        self._persist()

    # ------------------------------------------------------------------ views

    def bot_view(self, bot: BotRuntime) -> dict:
        view = {
            "id": bot.id, "name": bot.config["name"], "strategy": bot.config["strategy"],
            "title": bot.strategy.title, "role": bot.strategy.role, "summary": bot.strategy.summary,
            "account_id": bot.config["account_id"], "symbols": bot.config["symbols"],
            "enabled": bot.config.get("enabled", True), "paused_reason": bot.paused_reason,
            "params": bot.strategy.p, "last_action": bot.last_action,
            "last": self.last_said.get(bot.id),
        }
        if bot.ledger:
            snap = bot.ledger.snapshot(self.prices)
            view.update(limits=bot.limits.to_dict(), profile=bot.config.get("profile", "balanced"), **snap,
                        pnl=snap["equity"] - snap["budget"],
                        pnl_pct=(snap["equity"] / snap["budget"] - 1) if snap["budget"] else 0)
        else:
            view["reputation"] = self.council.rep(bot.id).to_dict()
            view["trust"] = self.council.profile(bot.id)
        return view

    def overview(self) -> dict:
        bots = [self.bot_view(b) for b in self.bots.values()]
        traders = [b for b in bots if b["role"] == "trader"]
        markets = {}
        now = now_ms()
        for key, price in self.prices.items():
            c = self.council.consensus(key, now, self.bar_ms.get(key, 60_000), self.regime.get(key))
            markets[key] = {"price": price, "direction": c.direction, "risk_off": c.risk_off,
                            "voices": len(c.voices), "regime": self.regime.get(key)}
        return {
            "halted": self.risk.halted,
            "running": self.running,
            "accounts": [a.public() for a in self.accounts.values()],
            "bots": bots,
            "markets": markets,
            "totals": {
                "budget": sum(t["budget"] for t in traders),
                "equity": sum(t["equity"] for t in traders),
                "traders": len(traders),
                "testnet": any(a.record.get("sandbox") for a in self.accounts.values()
                               if any(b.config["account_id"] == a.id for b in self.bots.values())),
                "running": sum(1 for b in bots if b["enabled"] and not b["paused_reason"]),
            },
        }

    def candles(self, key: str) -> list[float]:
        s = self.series.get(key)
        return [] if s is None else [float(x) for x in s.close[-160:]]
