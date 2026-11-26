"""Notifications and remote control: Telegram (both ways) and Discord (outgoing only).

Telegram: the owner creates a bot with @BotFather, pastes its token in
Settings, then sends the pairing code to that bot. From then on only that
one chat may give commands; everybody else is ignored.

Commands: /status, /start, /stop (keeps positions), /stop close, /help.

Discord: a channel webhook receives the same messages. Webhooks cannot
read replies, so Discord has no commands.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from .engine import ConfigError

if TYPE_CHECKING:
    from .bus import Bus
    from .engine import Engine
    from .lead import Lead
    from .security import Vault
    from .storage import Store

log = logging.getLogger(__name__)

TELEGRAM = "https://api.telegram.org"
DISCORD_HOOK = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")
TOKEN_SHAPE = re.compile(r"^\d{5,}:[\w-]{30,}$")
EVENTS = {"fills": "Every buy and sell", "stops": "Stop losses", "pauses": "A trader pauses",
          "errors": "Orders that fail", "summary": "Daily summary"}
REPEAT_AFTER_S = 600  # the same error is sent at most once per ten minutes


def _money(v: float) -> str:
    return f"{v:,.2f}"


class Notifier:
    def __init__(self, engine: Engine, lead: Lead, store: Store, vault: Vault, bus: Bus) -> None:
        self.engine, self.lead, self.store, self.vault = engine, lead, store, vault
        self.http = httpx.AsyncClient(timeout=35)
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self.pair_code = ""
        self._tasks: list[asyncio.Task] = []
        self._poller: asyncio.Task | None = None
        self._recent: dict[str, float] = {}
        self.last_error = ""
        bus.subscribe("*", self._hear)

    # ------------------------------------------------------------------ settings

    def _sealed(self, name: str, field: str) -> str:
        token = self.store.setting(name, "")
        return self.vault.open(token).get(field, "") if token else ""

    @property
    def telegram_token(self) -> str:
        return self._sealed("telegram_token", "token")

    @property
    def telegram_chat(self) -> int | None:
        return self.store.setting("telegram_chat")

    @property
    def discord_url(self) -> str:
        return self._sealed("discord_webhook", "url")

    def events(self) -> dict[str, bool]:
        saved = self.store.setting("notify_events", {})
        return {k: bool(saved.get(k, True)) for k in EVENTS}

    def settings(self) -> dict:
        token = self.telegram_token
        return {
            "telegram": {"configured": bool(token), "paired": self.telegram_chat is not None,
                         "pair_code": self.pair_code if token and self.telegram_chat is None else "",
                         "hint": f"{token.split(':', 1)[0]}:…" if token else ""},
            "discord": {"configured": bool(self.discord_url)},
            "events": self.events(), "event_labels": EVENTS,
            "summary_hour": int(self.store.setting("summary_hour", 20)),
            "last_error": self.last_error,
        }

    def update(self, data: dict) -> dict:
        if "telegram_token" in data:
            token = str(data["telegram_token"] or "").strip()
            if token and not TOKEN_SHAPE.match(token):
                raise ConfigError("That does not look like a Telegram bot token. It reads like 123456789:AA…")
            self.store.set_setting("telegram_token", self.vault.seal({"token": token}) if token else "")
            self.store.set_setting("telegram_chat", None)  # a new bot must be paired again
            self.pair_code = f"{secrets.randbelow(900000) + 100000}" if token else ""
            self._restart_poller()
        if "discord_webhook" in data:
            url = str(data["discord_webhook"] or "").strip()
            if url and not DISCORD_HOOK.match(url):
                raise ConfigError("Paste the full webhook URL from Discord: Channel settings, Integrations, Webhooks")
            self.store.set_setting("discord_webhook", self.vault.seal({"url": url}) if url else "")
        if isinstance(data.get("events"), dict):
            self.store.set_setting("notify_events", {k: bool(data["events"].get(k, True)) for k in EVENTS})
        if data.get("summary_hour") is not None:
            self.store.set_setting("summary_hour", max(0, min(23, int(data["summary_hour"]))))
        return self.settings()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self.telegram_token and self.telegram_chat is None:
            self.pair_code = f"{secrets.randbelow(900000) + 100000}"
        self._tasks = [asyncio.create_task(self._send_loop(), name="quorum-notify"),
                       asyncio.create_task(self._summary_loop(), name="quorum-summary")]
        self._restart_poller()

    async def stop(self) -> None:
        for task in [*self._tasks, self._poller]:
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        await self.http.aclose()

    def _restart_poller(self) -> None:
        if self._poller:
            self._poller.cancel()
            self._poller = None
        if self.telegram_token:
            self._poller = asyncio.create_task(self._poll_telegram(), name="quorum-telegram")

    # ------------------------------------------------------------------ outgoing

    def send(self, text: str) -> None:
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("Notification queue full; dropping: %s", text[:80])

    async def send_now(self, text: str) -> None:
        """Deliver to every configured channel; raise if none worked."""
        token, chat, hook = self.telegram_token, self.telegram_chat, self.discord_url
        if not ((token and chat is not None) or hook):
            raise ConfigError("Connect Telegram (and send the pairing code) or add a Discord webhook first")
        problems = []
        if token and chat is not None:
            r = await self.http.post(f"{TELEGRAM}/bot{token}/sendMessage", json={"chat_id": chat, "text": text})
            if r.status_code != 200:
                problems.append(f"Telegram: {r.json().get('description', r.status_code)}")
        if hook:
            r = await self.http.post(hook, json={"content": text[:1900], "username": "Quorum"})
            if r.status_code >= 300:
                problems.append(f"Discord: HTTP {r.status_code}")
        if problems:
            self.last_error = "; ".join(problems)
            raise RuntimeError(self.last_error)
        self.last_error = ""

    async def _send_loop(self) -> None:
        while True:
            text = await self.queue.get()
            try:
                await self.send_now(text)
            except ConfigError:
                pass  # nothing configured: notifications are optional
            except Exception as exc:
                log.warning("Notification failed: %s", exc)
            await asyncio.sleep(0.4)  # Telegram allows about one message a second per chat

    def _hear(self, msg: dict) -> None:
        if not (self.telegram_token or self.discord_url):
            return
        text = self._describe(msg["topic"], msg["data"])
        if text:
            self.send(text)

    def _describe(self, topic: str, d: dict[str, Any]) -> str | None:
        on = self.events()
        if topic == "fill":
            stop = "stop" in d["reason"].lower()
            if not (on["stops"] if stop else on["fills"]):
                return None
            symbol = d["instrument_key"].split(":", 1)[1]
            verb = "bought" if d["side"] == "buy" else "sold"
            text = (f"{d['bot_name']} {verb} {d['qty']:.6g} {symbol} at {d['price']:.6g} "
                    f"({_money(d['qty'] * d['price'])}).")
            if d.get("pnl"):
                text += f" Result {d['pnl']:+,.2f}."
            return f"{text}\n{d['reason']}"
        if topic == "bot" and d.get("event") == "paused" and on["pauses"]:
            return f"{d['name']} paused: {d['reason']}"
        if topic == "error" and on["errors"]:
            text = f"{d.get('bot_name', 'Quorum')}: {d['message']}"
            now = time.time()
            if now - self._recent.get(text, 0) < REPEAT_AFTER_S:
                return None
            self._recent[text] = now
            return text
        if topic == "council":
            if d.get("running"):
                return "The council is running. Analysts are reading the markets and traders may act."
            return "The council stopped." + (" Every position was closed." if d.get("closed") else
                                             " Open positions keep their stop losses.")
        if topic == "halt" and d.get("on"):
            return "Kill switch is on. No new orders." + (" Positions were closed." if d.get("flatten") else "")
        return None

    # ------------------------------------------------------------------ daily summary

    def summary(self) -> str:
        ov = self.engine.overview()
        traders = [b for b in ov["bots"] if b["role"] == "trader"]
        if not traders:
            return "No team yet. Finish the setup with the Chair."
        budget, equity = ov["totals"]["budget"], ov["totals"]["equity"]
        lines = [f"Team worth {_money(equity)} on a budget of {_money(budget)} "
                 f"({(equity / budget - 1) * 100 if budget else 0:+.2f}%)."]
        day = self.store.setting("summary_day") or {}
        if day.get("equity"):
            lines.append(f"Since this time yesterday: {(equity / day['equity'] - 1) * 100:+.2f}%.")
        best = max(traders, key=lambda b: b["pnl_pct"])
        lines.append(f"Best trader: {best['name']} at {best['pnl_pct'] * 100:+.2f}%.")
        scored = [b for b in ov["bots"] if b["role"] == "analyst" and b["reputation"]["calls"] >= 10]
        if scored:
            top = max(scored, key=lambda b: b["reputation"]["accuracy"])
            lines.append(f"Most accurate analyst: {top['name']}, right {top['reputation']['accuracy']:.0%} "
                         f"of {top['reputation']['calls']} calls.")
        held = [f"{k.split(':', 1)[1]} ({b['name']})" for b in traders for k in b["positions"]]
        lines.append("Holding: " + ", ".join(held) + "." if held else "No open positions.")
        if not ov["running"]:
            lines.append("The council is stopped.")
        return "\n".join(lines)

    async def _summary_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                if now.hour != int(self.store.setting("summary_hour", 20)):
                    continue
                if self.store.setting("summary_sent") == today:
                    continue
                self.store.set_setting("summary_sent", today)
                if self.events()["summary"]:
                    self.send(f"Daily summary, {now:%d %b}\n" + self.summary())
                self.store.set_setting("summary_day", {"day": today,
                                                        "equity": self.engine.overview()["totals"]["equity"]})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Daily summary failed")

    # ------------------------------------------------------------------ incoming (Telegram)

    async def _poll_telegram(self) -> None:
        token, offset = self.telegram_token, 0
        while True:
            try:
                r = await self.http.get(f"{TELEGRAM}/bot{token}/getUpdates",
                                        params={"timeout": 25, "offset": offset, "allowed_updates": '["message"]'})
                body = r.json()
                if not body.get("ok"):
                    self.last_error = f"Telegram: {body.get('description', 'token rejected')}"
                    await asyncio.sleep(30)
                    continue
                for update in body["result"]:
                    offset = update["update_id"] + 1
                    msg = update.get("message") or {}
                    text, chat = (msg.get("text") or "").strip(), (msg.get("chat") or {}).get("id")
                    if text and chat is not None:
                        await self._command(chat, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Telegram polling failed: %s", exc)
                await asyncio.sleep(10)

    async def _reply(self, chat: int, text: str) -> None:
        await self.http.post(f"{TELEGRAM}/bot{self.telegram_token}/sendMessage", json={"chat_id": chat, "text": text})

    async def _command(self, chat: int, text: str) -> None:
        word, _, rest = text.partition(" ")
        word = word.split("@", 1)[0].lower()
        paired = self.telegram_chat
        if paired is None:
            if word == "/pair" and self.pair_code and rest.strip() == self.pair_code:
                self.store.set_setting("telegram_chat", chat)
                self.pair_code = ""
                await self._reply(chat, "Paired. This chat now gets Quorum's messages and may send /status, "
                                        "/start and /stop.")
            elif word in ("/pair", "/start"):
                await self._reply(chat, "Send /pair followed by the code shown in Quorum's Settings.")
            return
        if chat != paired:
            return  # a stranger found the bot: say nothing
        if word == "/status":
            await self._reply(chat, ("Running." if self.engine.running else "Stopped.") + "\n" + self.summary())
        elif word == "/start":
            if not self.lead.onboarding.done:
                await self._reply(chat, "Finish the setup in the dashboard first.")
            elif self.engine.running:
                await self._reply(chat, "Already running.")
            else:
                self.engine.start_council()
                self.lead.say("Started from Telegram.")
        elif word == "/stop":
            if not self.engine.running:
                await self._reply(chat, "Already stopped.")
                return
            close = rest.strip().lower() == "close"
            await self.engine.stop_council(close_positions=close)
            self.lead.say("Stopped from Telegram." + (" Every position was closed." if close else ""))
        else:
            await self._reply(chat, "/status  how the team is doing\n/start  let the council trade\n"
                                    "/stop  stop, keep positions and their stop losses\n/stop close  stop and sell everything")
