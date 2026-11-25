"""First-run setup, run by the Chair as a conversation.

market -> venue -> connect (API keys) -> markets -> budget -> team -> risk -> ready -> live

Every trader places real orders. To practise, connect a testnet account.
Nothing analyses or trades until the owner presses Start at the end. Keys
are submitted through a form card straight to the account store; they
never appear in the chat history.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .bots import TRADERS
from .connectors import NEEDS_PASSPHRASE, SUGGESTED_MARKETS, VENUE_NAMES, VENUES
from .engine import ConfigError
from .risk import PROFILES

if TYPE_CHECKING:
    from .engine import Engine
    from .lead import Lead
    from .storage import Store

STEPS = ("market", "venue", "connect", "markets", "budget", "team", "risk", "ready", "done")

# Who to hire first when the budget only stretches to a few traders: the steadiest styles lead.
HIRING_ORDER = ["council", "trend_rider", "breakout", "mean_reverter", "accumulator"]
TRADER_HINTS = {
    "council": "Only trades when the analysts strongly agree. A calm first hire.",
    "trend_rider": "Follows trends when the team agrees on direction.",
    "breakout": "Buys new highs, gets out fast when they fail.",
    "mean_reverter": "Buys sharp drops and sells the bounce.",
    "accumulator": "Buys a little at a time and takes profit at a set gain.",
}

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
            default = round(min(100.0, balance), 2) if balance else 100.0
            where = "testnet funds" if acc.record.get("sandbox") else "real money from your account"
            self.lead.prompt("budget", f"How much should the team work with? The traders place real orders with {where}.",
                             {"type": "form", "submit": "Continue", "fields": [
                                 {"name": "budget", "label": "Budget for the team", "kind": "number", "value": default,
                                  "hint": (f"Free on the account: {balance:,.2f}. " if balance is not None else "")
                                          + f"At least {self._floor():g}. Whatever you don't give the team stays untouched."},
                                 {"name": "confirm", "label": "I understand the traders can lose this money.", "kind": "check"},
                             ]})
        elif step == "team":
            budget, floor = s["budget"], self._floor()
            fits = max(1, min(len(HIRING_ORDER), int(budget // (floor * 1.6))))
            options = [{"value": k, "label": f"{TRADERS[k].persona} · {TRADERS[k].title}", "hint": TRADER_HINTS[k]}
                       for k in HIRING_ORDER]
            advice = ("All five fit comfortably." if fits == len(HIRING_ORDER) else
                      f"With {budget:,.2f}, {fits} trader{'s' if fits > 1 else ''} keep{'s' if fits == 1 else ''} every order "
                      "comfortably above the exchange minimum, so I picked those.")
            self.lead.prompt("team", f"Who should trade for you? The budget is split evenly between them. {advice}",
                             {"type": "multi", "options": options, "selected": HIRING_ORDER[:fits], "submit": "Hire them"})
        elif step == "risk":
            options = [{"value": k, "label": p["label"], "hint": p["hint"]} for k, p in PROFILES.items()]
            self.lead.prompt("risk", "How careful should they be? You can change this per trader later.",
                             {"type": "choice", "options": options})
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
                    {"name": "sandbox", "label": "This key is for an Alpaca practice (paper) account", "kind": "check", "value": True}]
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

    def _floor(self) -> float:
        s = self.state
        return self.engine.min_trader_budget(s["account_id"], s.get("symbols") or [])

    async def _on_budget(self, value: dict) -> None:
        s = self.state
        try:
            budget = round(float(value.get("budget")), 2)
        except (TypeError, ValueError):
            raise ConfigError("Enter the budget as a number") from None
        if budget < self._floor():
            raise ConfigError(f"The budget must be at least {self._floor():g} so orders clear the exchange minimum")
        acc = self.engine.accounts[s["account_id"]]
        if acc.status and acc.status.balance_quote is not None and budget > acc.status.balance_quote:
            raise ConfigError(f"The account only has {acc.status.balance_quote:,.2f} free")
        if not value.get("confirm"):
            raise ConfigError("Tick the box to confirm the traders can lose this money")
        self.lead.owner_says(f"{budget:,.2f} for the team")
        self._save(budget=budget, step="team")
        self._ask("team")

    async def _on_team(self, value: dict) -> None:
        s = self.state
        picks = [k for k in HIRING_ORDER if k in (value.get("choices") or [])]
        if not picks:
            raise ConfigError("Hire at least one trader")
        share = s["budget"] / len(picks)
        if share < self._floor():
            raise ConfigError(f"That leaves {share:,.2f} each, below the {self._floor():g} a trader needs. Hire fewer")
        self.lead.owner_says(", ".join(TRADERS[k].persona for k in picks))
        self._save(traders=picks, step="risk")
        self._ask("risk")

    async def _on_risk(self, value: dict) -> None:
        s = self.state
        profile = value.get("choice")
        if profile not in PROFILES:
            raise ConfigError("Pick how careful the traders should be")
        self.engine.create_team(s["account_id"], s["symbols"], s["budget"], s["traders"], profile)
        self.lead.owner_says(PROFILES[profile]["label"])
        share = s["budget"] / len(s["traders"])
        names = ", ".join(TRADERS[k].persona for k in s["traders"])
        self.lead.say(
            f"The team is assembled. 9 analysts will watch {', '.join(s['symbols'])}, and {names} "
            f"{'trades' if len(s['traders']) == 1 else 'trade'} with {share:,.2f} each on the "
            f"{PROFILES[profile]['label'].lower()} profile. Every trader has a stop loss, a daily loss limit and a "
            "drawdown limit, and you can change them on its page.")
        self._save(profile=profile, step="ready")
        self._ask("ready")

    async def _on_ready(self, value: dict) -> None:
        if value.get("choice") != "start":
            raise ConfigError("Press Start when you are ready")
        self.lead.owner_says("Start")
        self.engine.start_council()
        self._save(step="done")
        s = self.state
        self.lead.say(
            f"The council is live. Analysts read {', '.join(s['symbols'])} on every new candle, traders place real "
            "orders within their budgets, and I'll brief you when something changes. Ask me anything, or say stop "
            "at any time.")
