"""First-run setup, run by the Chair as a conversation.

market -> venue -> connect (API keys) -> markets -> budget & mode -> ready -> live

Nothing analyses or trades until the owner presses Start at the end. Keys
are submitted through a form card straight to the account store; they
never appear in the chat history.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .connectors import NEEDS_PASSPHRASE, SUGGESTED_MARKETS, VENUE_NAMES, VENUES
from .engine import ConfigError
from .models import Market, Mode
from .risk import RiskLimits

if TYPE_CHECKING:
    from .engine import Engine
    from .lead import Lead
    from .storage import Store

STEPS = ("market", "venue", "connect", "markets", "budget", "ready", "done")
MIN_BUDGET = RiskLimits().min_order_value * 2 * 5  # every one of the 5 traders must afford two minimum orders

MARKET_OPTIONS = [
    {"value": "crypto", "label": "Crypto", "hint": "Spot markets on a crypto exchange, open 24/7"},
    {"value": "stocks", "label": "US stocks", "hint": "Shares and ETFs through Alpaca"},
    {"value": "forex", "label": "Forex", "hint": "Currency pairs through OANDA"},
]

KEY_HELP = {
    "binance": "Binance → Profile → API Management → Create API. Enable Spot trading only. Leave withdrawals off.",
    "bybit": "Bybit → Profile → API → Create New Key → System-generated. Grant Spot trading. Leave withdrawals off.",
    "okx": "OKX → Profile → API → Create API key. Permissions: Read and Trade. Not Withdraw.",
    "kraken": "Kraken → Settings → API → Generate key. Allow query and create/cancel orders. Not withdraw.",
    "coinbase": "Coinbase Advanced → Settings → API → New key with view and trade. Not transfer.",
    "kucoin": "KuCoin → API Management → Create API. General and Spot trading. Not transfer.",
    "bitget": "Bitget → API Management → Create. Read and Spot trade. Not withdraw.",
    "gate": "Gate.io → API Management → Create key. Spot trade. Not withdraw.",
    "mexc": "MEXC → API Management → Create. Spot trading. Not withdraw.",
    "alpaca": "Alpaca dashboard → API Keys → Generate. Paper and live accounts have separate keys.",
    "oanda": "OANDA → Manage API Access → Generate token. Your account ID is shown on the account page.",
}


class Onboarding:
    def __init__(self, lead: "Lead", engine: "Engine", store: "Store") -> None:
        self.lead, self.engine, self.store = lead, engine, store

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> dict[str, Any]:
        default = {"step": "done" if self.engine.bots else "market"}
        return self.store.setting("onboarding", default)

    def _save(self, **changes: Any) -> dict:
        s = {**self.state, **changes}
        self.store.set_setting("onboarding", s)
        return s

    @property
    def done(self) -> bool:
        return self.state["step"] == "done"

    def summary(self) -> dict:
        s = self.state
        return {"step": s["step"], "index": STEPS.index(s["step"]), "total": len(STEPS) - 1,
                "venue": VENUE_NAMES.get(s.get("venue", ""), ""), "market": s.get("market")}

    # ------------------------------------------------------------------ prompts

    def begin(self) -> None:
        """Greet a new owner, or re-ask the open question after a restart."""
        if self.done:
            return
        open_prompt = next((m for m in reversed(self.lead.history(40))
                            if m.get("kind") == "prompt" and not m.get("answered")), None)
        if open_prompt:
            return
        if self.state["step"] == "market":
            self.lead.say(
                "Welcome to Quorum. I'm the Chair: I run a council of nine analysts and five traders and report to you.\n"
                "Nothing will analyse or trade until you connect an account and tell me to start. "
                "Let's set it up. It takes about two minutes.")
        self._ask(self.state["step"])

    def _ask(self, step: str) -> None:
        s = self.state
        if step == "market":
            self.lead.prompt("market", "Which market do you want the council to trade?",
                             {"type": "choice", "options": MARKET_OPTIONS})
        elif step == "venue":
            options = [{"value": v, "label": VENUE_NAMES[v]} for v in VENUES[s["market"]]]
            self.lead.prompt("venue", "Which exchange or broker holds your account?", {"type": "choice", "options": options})
        elif step == "connect":
            self.lead.prompt("connect", f"Connect your {VENUE_NAMES[s['venue']]} account. Create an API key that can trade but cannot withdraw.",
                             {"type": "form", "fields": self._key_fields(s["market"], s["venue"]),
                              "help": KEY_HELP.get(s["venue"], ""), "submit": "Connect account"})
        elif step == "markets":
            acc = self.engine.accounts[s["account_id"]]
            options = [{"value": m, "label": m} for m in self._available(acc)]
            self.lead.prompt("markets", "Which markets should the council watch and trade? Fewer markets means more focus.",
                             {"type": "multi", "options": options, "selected": [o["value"] for o in options[:3]],
                              "submit": "Use these markets"})
        elif step == "budget":
            acc = self.engine.accounts[s["account_id"]]
            balance = acc.status.balance_quote if acc.status else None
            default = min(500.0, round(balance, 2)) if balance else 500.0
            self.lead.prompt("budget", "How much should the traders work with, and with what money?",
                             {"type": "form", "submit": "Assemble the team", "fields": [
                                 {"name": "budget", "label": "Total budget", "kind": "number", "value": default,
                                  "hint": (f"Free balance on the account: {balance:,.2f}. " if balance else "")
                                          + f"Split evenly across 5 traders. Minimum {MIN_BUDGET:g}."},
                                 {"name": "mode", "label": "Money", "kind": "choice", "value": "paper", "options": [
                                     {"value": "paper", "label": "Paper", "hint": "Real prices, simulated orders. Recommended first."},
                                     {"value": "live", "label": "Real money", "hint": "Real orders on your account."}]},
                                 {"name": "confirm", "label": "I understand the bots can lose this money.", "kind": "check",
                                  "show_if": {"mode": "live"}},
                             ]})
        elif step == "ready":
            self.lead.prompt("ready", "Everything is in place. The council stays idle until you start it.",
                             {"type": "choice", "options": [{"value": "start", "label": "Start the council", "primary": True}]})

    def _key_fields(self, market: str, venue: str) -> list[dict]:
        from .connectors.crypto import has_testnet

        if market == "crypto":
            fields = [{"name": "api_key", "label": "API key", "kind": "text"},
                      {"name": "secret", "label": "API secret", "kind": "secret"}]
            if venue in NEEDS_PASSPHRASE:
                fields.append({"name": "password", "label": "API passphrase", "kind": "secret"})
            if has_testnet(venue):
                fields.append({"name": "sandbox", "label": "This is a testnet key", "kind": "check",
                               "hint": "Testnet keys come from the exchange's separate test site."})
            return fields
        if market == "stocks":
            return [{"name": "api_key", "label": "API key ID", "kind": "text"},
                    {"name": "secret", "label": "Secret key", "kind": "secret"},
                    {"name": "sandbox", "label": "This is a paper-trading key", "kind": "check", "value": True}]
        return [{"name": "api_key", "label": "API token", "kind": "secret"},
                {"name": "account_id", "label": "Account ID", "kind": "text", "placeholder": "101-004-1234567-001"},
                {"name": "sandbox", "label": "This is a practice account", "kind": "check", "value": True}]

    def _available(self, acc) -> list[str]:
        suggested = SUGGESTED_MARKETS[acc.record["market"]]
        markets = getattr(getattr(acc.connector, "ex", None), "markets", None)
        if markets:  # crypto: keep only pairs this exchange actually lists
            listed = [m for m in suggested if m in markets]
            return listed or suggested
        return suggested

    # ------------------------------------------------------------------ answers

    async def answer(self, step: str, value: dict) -> None:
        s = self.state
        if step != s["step"]:
            raise ConfigError("That question has already been answered")
        handler = getattr(self, f"_on_{step}")
        await handler(value)

    async def _on_market(self, value: dict) -> None:
        market = value.get("choice")
        if market not in VENUES:
            raise ConfigError("Pick a market")
        self.lead.owner_says(next(o["label"] for o in MARKET_OPTIONS if o["value"] == market))
        self._save(market=market, step="venue")
        if len(VENUES[market]) == 1:  # stocks and forex have one broker each: skip the question
            venue = VENUES[market][0]
            self.lead.say(f"{VENUE_NAMES[venue]} is the broker Quorum supports for {market}.")
            self._save(venue=venue, step="connect")
        self._ask(self.state["step"])

    async def _on_venue(self, value: dict) -> None:
        venue = value.get("choice")
        if venue not in VENUES[self.state["market"]]:
            raise ConfigError("Pick one of the listed exchanges")
        self.lead.owner_says(VENUE_NAMES[venue])
        self._save(venue=venue, step="connect")
        self._ask("connect")

    async def _on_connect(self, value: dict) -> None:
        s = self.state
        key = str(value.get("api_key", "")).strip()
        if not key or not (value.get("secret") or value.get("account_id")):
            raise ConfigError("Fill in every key field")
        name = VENUE_NAMES[s["venue"]]
        sandbox = bool(value.get("sandbox"))
        self.lead.owner_says(f"Connecting {name}{' testnet' if sandbox else ''} with key …{key[-4:]}")
        try:
            acc = await self.engine.add_account({
                "name": f"{name}{' testnet' if sandbox else ''}", "market": s["market"], "venue": s["venue"],
                "sandbox": sandbox, "api_key": key, "secret": str(value.get("secret", "")),
                "password": str(value.get("password", "")), "account_id": str(value.get("account_id", "")),
                "symbols": SUGGESTED_MARKETS[s["market"]][:3],
            }, require_ok=True)
        except ConfigError as exc:
            self.lead.notice(f"{name} did not accept the key: {exc}")
            self._ask("connect")
            return
        st = acc.status
        if not st.can_trade:
            await self.engine.remove_account(acc.id)
            self.lead.notice(f"{name} accepted the key, but it cannot place orders. Enable trading on the key and try again.")
            self._ask("connect")
            return
        balance = f" Free balance: {st.balance_quote:,.2f}." if st.balance_quote is not None else ""
        withdraw = ("Withdrawals are disabled on this key." if st.can_withdraw is False else
                    f"{name} does not report key permissions, so double-check that withdrawals are off.")
        self.lead.say(f"Connected to {acc.record['name']}.{balance} {withdraw}")
        self._save(account_id=acc.id, step="markets")
        self._ask("markets")

    async def _on_markets(self, value: dict) -> None:
        picks = [m for m in value.get("choices", []) if isinstance(m, str)]
        if not picks:
            raise ConfigError("Pick at least one market")
        self.engine.set_account_symbols(self.state["account_id"], picks)
        self.lead.owner_says(", ".join(picks))
        self._save(symbols=picks, step="budget")
        self._ask("budget")

    async def _on_budget(self, value: dict) -> None:
        s = self.state
        try:
            budget = round(float(value.get("budget")), 2)
        except (TypeError, ValueError):
            raise ConfigError("Enter the budget as a number") from None
        if budget < MIN_BUDGET:
            raise ConfigError(f"The budget must be at least {MIN_BUDGET:g}")
        mode = Mode(value.get("mode", "paper"))
        acc = self.engine.accounts[s["account_id"]]
        if mode == Mode.LIVE:
            if not value.get("confirm"):
                raise ConfigError("Tick the confirmation to trade real money")
            if acc.status and acc.status.balance_quote is not None and budget > acc.status.balance_quote:
                raise ConfigError(f"The account only has {acc.status.balance_quote:,.2f} free")
        self.engine.create_team(s["account_id"], s["symbols"], budget, mode)
        label = "real money" if mode == Mode.LIVE else "paper"
        self.lead.owner_says(f"{budget:,.2f} on {label}")
        share = budget / 5
        self.lead.say(
            f"The council is assembled: 9 analysts watching {', '.join(s['symbols'])}, and 5 traders with "
            f"{share:,.2f} each on {label}. Every trader has a stop loss, a daily loss limit and a drawdown limit; "
            "you can change them on each trader's page.")
        self._save(budget=budget, mode=mode.value, step="ready")
        self._ask("ready")

    async def _on_ready(self, value: dict) -> None:
        if value.get("choice") != "start":
            raise ConfigError("Press Start when you are ready")
        self.lead.owner_says("Start")
        self.engine.start_council()
        self._save(step="done")
        s = self.state
        self.lead.say(
            f"The council is live. Analysts read {', '.join(s['symbols'])} on every new candle, traders act on their own "
            "budgets, and I'll brief you when something changes. Ask me anything, or say stop at any time.")
