"""The Chair: lead of the council and the owner's single point of contact.

The Chair never trades. It consults the analysts, reads the traders'
books, and reports to the owner in plain language. Anything that would
change money (pausing a bot, changing a budget) is a *proposal* the
owner approves with one click; the Chair cannot apply it on its own.

Two voices:
  * rules  - deterministic briefings built from the engine state; free,
             works offline, always available.
  * claude - the same facts handed to Claude, which writes the answer.
             Opt-in, uses the owner's own Anthropic API key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import anthropic

from .bots import Analyst
from .engine import ConfigError
from .models import new_id, now_ms

if TYPE_CHECKING:
    from .bus import Bus
    from .engine import Engine
    from .security import Vault
    from .storage import Store

log = logging.getLogger(__name__)

NAME = "Chair"
DEFAULT_MODEL = "claude-opus-5-5"
FIRST_BRIEFING_S = 25
START_WORDS = re.compile(r"^\s*(start|go|begin|resume)\b", re.I)
STOP_WORDS = re.compile(r"^\s*(stop|pause|halt)\b", re.I)
TRADER_QUESTION = re.compile(r"\b(trader|bot|budget|profit|loss|perform|doing|pnl|portfolio|money)", re.I)

SYSTEM_PROMPT = """You are the Chair, lead of Quorum: a council of trading bots running on the owner's own computer.
Analysts read markets and vote; traders hold small budgets and trade; you report to the owner.

You receive a JSON snapshot of the council: markets with the reputation-weighted vote (-1 bearish to +1 bullish)
and risk warnings, fresh analyst readings, every trader's budget, equity, return, win rate and risk profile (all traders place real orders, on a testnet
when the account is one), and each analyst's measured accuracy, overall and per market.

How to answer:
- Use only facts present in the snapshot. Quote the numbers. If the snapshot cannot answer the question, say so.
- Lead with the answer. Then at most three numbered points, each starting with a bold verb, e.g. "1. **Watch** BTC/USDT: ...".
- Weigh analysts by their accuracy; say when they disagree.
- Never promise or predict profit. Testnet results are not real-money results. Mention risk when it is material.
- You cannot trade or change settings. If an action makes sense, recommend it in words; the owner decides.
- Plain text only, apart from **bold** verbs. Keep it under 180 words."""


def _fingerprint(items: list[dict]) -> str:
    return hashlib.sha1(json.dumps([(i["verb"], i.get("target")) for i in items]).encode()).hexdigest()


class Lead:
    def __init__(self, engine: "Engine", store: "Store", vault: "Vault", bus: "Bus") -> None:
        self.engine, self.store, self.vault, self.bus = engine, store, vault, bus
        self._task: asyncio.Task | None = None
        self._last_fingerprint = ""
        self._busy = asyncio.Lock()
        self._jobs: set[asyncio.Task] = set()  # keep references so answers are not garbage-collected

    # ------------------------------------------------------------------ settings

    def settings(self) -> dict:
        sealed = self.store.setting("claude_key", "")
        key = self.vault.open(sealed).get("key", "") if sealed else ""
        return {
            "claude": {"configured": bool(key), "key_hint": f"{key[:7]}…{key[-4:]}" if key else "",
                       "model": self.store.setting("claude_model", DEFAULT_MODEL)},
            "briefing_minutes": float(self.store.setting("briefing_minutes", 5)),
        }

    def update_settings(self, data: dict) -> dict:
        if data.get("clear_key"):
            self.store.set_setting("claude_key", "")
        elif data.get("claude_key"):
            self.store.set_setting("claude_key", self.vault.seal({"key": str(data["claude_key"]).strip()}))
        if data.get("model"):
            self.store.set_setting("claude_model", str(data["model"]).strip())
        if data.get("briefing_minutes"):
            self.store.set_setting("briefing_minutes", max(1.0, float(data["briefing_minutes"])))
        return self.settings()

    def _claude_key(self) -> str:
        sealed = self.store.setting("claude_key", "")
        return self.vault.open(sealed).get("key", "") if sealed else ""

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        from .onboarding import Onboarding  # imported late: onboarding depends on the engine module

        self.onboarding = Onboarding(self, self.engine, self.store)
        self.onboarding.begin()
        self._task = asyncio.create_task(self._run(), name="quorum-lead")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        await asyncio.sleep(FIRST_BRIEFING_S)
        while True:
            try:
                if self.engine.running:
                    await self.brief(force=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Briefing failed")
            await asyncio.sleep(self.settings()["briefing_minutes"] * 60)

    # ------------------------------------------------------------------ chat plumbing

    def history(self, limit: int = 120) -> list[dict]:
        return self.store.chat(limit)

    def _post(self, msg: dict) -> dict:
        self.store.save_chat(msg)
        self.bus.publish("chat", msg, keep=False)
        return msg

    def _message(self, role: str, kind: str, text: str = "", **extra: Any) -> dict:
        return {"id": new_id("msg"), "ts": now_ms(), "role": role, "kind": kind, "text": text, **extra}

    def notice(self, text: str) -> dict:
        return self._post(self._message("lead", "notice", text))

    def say(self, text: str) -> dict:
        return self._post(self._message("lead", "say", text))

    def owner_says(self, text: str) -> dict:
        return self._post(self._message("owner", "text", text))

    def prompt(self, step: str, text: str, spec: dict) -> dict:
        """A question the owner answers with buttons or a form card instead of free text."""
        return self._post(self._message("lead", "prompt", text, step=step, prompt=spec, answered=None))

    async def answer_prompt(self, msg_id: str, value: dict) -> dict:
        msg = self.store.chat_message(msg_id)
        if not msg or msg.get("kind") != "prompt":
            raise KeyError(msg_id)
        if msg.get("answered"):
            raise ConfigError("That question has already been answered")
        if msg["step"] == "stop":
            await self._on_stop(value)
        else:
            await self.onboarding.answer(msg["step"], value)
        msg["answered"] = now_ms()
        return self._post(msg)

    async def _on_stop(self, value: dict) -> None:
        choice = value.get("choice")
        if choice not in ("keep", "close"):
            raise ConfigError("Pick how to stop")
        self.owner_says("Stop and close every position" if choice == "close" else "Stop, keep positions")
        await self.engine.stop_council(close_positions=choice == "close")
        self.say("The council is stopped. Nothing analyses or opens trades. "
                 + ("Every position was closed." if choice == "close"
                    else "Open positions keep their stop losses.")
                 + " Say start when you want it back.")

    def _command(self, text: str) -> bool:
        """Handle start/stop. Returns True when the text was a command."""
        if START_WORDS.match(text):
            self.owner_says(text)
            if self.engine.running:
                self.say("The council is already running.")
            else:
                self.engine.start_council()
                self.say("The council is live again. Analysts are reading the markets and traders may act.")
            return True
        if STOP_WORDS.match(text):
            self.owner_says(text)
            if not self.engine.running:
                self.say("The council is already stopped.")
                return True
            self.prompt("stop", "How should I stop?", {"type": "choice", "options": [
                {"value": "keep", "label": "Stop, keep positions", "hint": "Stop losses stay active"},
                {"value": "close", "label": "Stop and close everything", "hint": "Sell all open positions now"}]})
            return True
        return False

    # ------------------------------------------------------------------ owner actions

    async def ask(self, text: str) -> dict | None:
        text = text.strip()[:2000]
        if not self.onboarding.done:
            if START_WORDS.match(text) and self.onboarding.state["step"] == "ready":
                await self.onboarding.answer("ready", {"choice": "start"})
                for m in reversed(self.history(20)):
                    if m.get("kind") == "prompt" and m.get("step") == "ready" and not m.get("answered"):
                        m["answered"] = now_ms()
                        self._post(m)
                return None
            self.owner_says(text)
            self.say("Let's finish the setup first: answer the question above, then I'm all yours.")
            return None
        if self._command(text):
            return None
        question = self._post(self._message("owner", "text", text))
        job = asyncio.create_task(self._answer_safely(text))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        return question

    async def _answer_safely(self, question: str) -> None:
        try:
            await self._answer(question)
        except Exception:
            log.exception("Answering failed")
            self._notice("Something went wrong while consulting the team. Check the engine log.")

    async def brief(self, force: bool = True) -> dict | None:
        async with self._busy:
            items = self._briefing_items()
            if not items:
                return None
            fp = _fingerprint(items)
            if not force and fp == self._last_fingerprint:
                return None  # nothing new worth interrupting the owner for
            self._last_fingerprint = fp
            proposals = [p for i in items for p in i.pop("proposals", [])]
            headline = (f"{len(items)} thing{'s' if len(items) > 1 else ''} worth your attention."
                        if len(items) > 1 else "One thing worth your attention.")
            return self._post(self._message("lead", "briefing", headline, items=items,
                                            proposals=proposals, source="rules"))

    def apply_proposal(self, msg_id: str, proposal_id: str) -> dict:
        msg = self.store.chat_message(msg_id)
        if not msg:
            raise KeyError(msg_id)
        prop = next((p for p in msg.get("proposals", []) if p["id"] == proposal_id), None)
        if prop is None:
            raise KeyError(proposal_id)
        if prop.get("applied"):
            return msg
        action = prop["action"]
        if action["type"] == "pause":
            self.engine.update_bot(action["bot_id"], {"enabled": False})
        elif action["type"] == "resume":
            self.engine.update_bot(action["bot_id"], {"enabled": True})
        elif action["type"] == "budget":
            self.engine.update_bot(action["bot_id"], {"budget": action["value"]})
        else:
            raise ValueError(f"Unknown proposal type {action['type']}")
        prop["applied"] = now_ms()
        return self._post(msg)

    # ------------------------------------------------------------------ consulting the team

    def _match_markets(self, text: str) -> list[str]:
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        hits = []
        for key in self.engine.prices:
            symbol = key.split(":", 1)[1].lower()
            parts = set(re.findall(r"[a-z0-9]+", symbol)) - {"sim", "usdt", "usd", "usdc"}
            if parts & words:
                hits.append(key)
        return hits

    async def _answer(self, question: str) -> None:
        markets = self._match_markets(question)
        if not markets and TRADER_QUESTION.search(question) and not self._claude_key():
            text, items = self._trader_answer()
            self._post(self._message("lead", "answer", text, items=items, source="rules"))
            return
        scope = markets or self._hot_markets(3)
        analysts = [b for b in self.engine.bots.values()
                    if not b.is_trader and b.active and any(k in b.keys for k in scope)]
        label = ", ".join(k.split(":", 1)[1] for k in scope) or "the markets"
        task = self._post(self._message("lead", "task", f"Consulting {len(analysts)} analysts on {label}",
                                        steps=[{"bot_id": b.id, "name": b.config["name"], "status": "running"}
                                               for b in analysts], status="running"))
        readings: dict[str, list[dict]] = {k: [] for k in scope}
        for i, bot in enumerate(analysts):
            assert isinstance(bot.strategy, Analyst)
            # Cross-market analysts need everything they watch, not just the markets asked about.
            watched = {k: self.engine.series[k] for k in bot.keys if k in self.engine.series}
            started = time.perf_counter()
            result = bot.strategy.read_all(watched) if watched else {}
            elapsed = (time.perf_counter() - started) * 1000
            notes = []
            for key, r in result.items():
                if key not in readings:
                    continue
                readings[key].append({"analyst": bot.config["name"], "kind": r.kind, "direction": round(r.direction, 3),
                                      "confidence": round(r.confidence, 3), "note": r.note,
                                      # how often this analyst has been right on this market, not overall
                                      "accuracy": round(self.engine.council.rep(bot.id, f"m:{key}").shrunk(
                                          self.engine.council.rep(bot.id).accuracy), 3)})
                notes.append(r.note)
            task["steps"][i].update(status="done", ms=round(elapsed, 2), note=notes[0] if notes else "No view")
            self._post(task)
            await asyncio.sleep(0)  # let the UI receive each step as it lands

        snapshot = self._snapshot(scope, readings)
        key = self._claude_key()
        text, items, source = None, [], "rules"
        if key:
            task["steps"].append({"name": NAME, "status": "running", "note": "Writing the answer with Claude"})
            self._post(task)
            started = time.perf_counter()
            text = await self._ask_claude(key, question, snapshot)
            task["steps"][-1].update(status="done" if text else "failed", ms=round((time.perf_counter() - started) * 1000),
                                     note="Answer written" if text else "Claude unavailable, answered from rules")
            source = "claude" if text else "rules"
        if not text:
            text, items = self._rule_answer(scope, readings)
        task["status"] = "done"
        self._post(task)
        self._post(self._message("lead", "answer", text, items=items, source=source, reply_to=task["id"]))

    async def _ask_claude(self, key: str, question: str, snapshot: dict) -> str | None:
        client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=90)
        try:
            response = await client.beta.messages.create(
                model=self.settings()["claude"]["model"],
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content":
                           f"<snapshot>\n{json.dumps(snapshot, separators=(',', ':'))}\n</snapshot>\n\n"
                           f"Owner's question: {question}"}],
            )
        except anthropic.AuthenticationError:
            self._notice("Claude rejected the API key. Update it in Settings.")
            return None
        except anthropic.RateLimitError:
            self._notice("Claude is rate limiting this key. The Chair answered from rules instead.")
            return None
        except anthropic.APIStatusError as exc:
            log.warning("Claude API error %s: %s", exc.status_code, exc.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("Cannot reach the Claude API")
            return None
        finally:
            await client.close()
        if response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None

    def _notice(self, text: str) -> None:
        self.notice(text)

    # ------------------------------------------------------------------ facts

    def _hot_markets(self, n: int) -> list[str]:
        now = now_ms()
        scored = []
        for key in self.engine.prices:
            c = self.engine.council.consensus(key, now, self.engine.bar_ms.get(key, 60_000))
            scored.append((abs(c.direction) * (len(c.voices) > 0) + c.risk_off * 0.5, key))
        return [k for _, k in sorted(scored, reverse=True)[:n]]

    def _snapshot(self, scope: list[str], readings: dict[str, list[dict]]) -> dict:
        ov = self.engine.overview()
        return {
            "markets": {k.split(":", 1)[1]: {**{kk: round(v, 4) if isinstance(v, float) else v
                                               for kk, v in ov["markets"].get(k, {}).items()},
                                            "readings": readings.get(k, [])} for k in scope},
            "traders": [{"name": b["name"], "strategy": b["title"], "profile": b["profile"], "budget": b["budget"],
                         "equity": round(b["equity"], 2), "return_pct": round(b["pnl_pct"] * 100, 2),
                         "closed_trades": b["trades"], "win_rate": b["win_rate"], "paused": b["paused_reason"],
                         "open_positions": {k.split(":", 1)[1]: round(p["qty"], 6) for k, p in b["positions"].items()},
                         "markets": b["symbols"]}
                        for b in ov["bots"] if b["role"] == "trader"],
            "analysts": [{"name": b["name"], "accuracy": round(b["reputation"]["accuracy"], 3),
                          "weight": round(b["reputation"]["weight"], 2), "scored_calls": b["reputation"]["calls"],
                          "by_market": {m["name"].split(":", 1)[-1]: round(m["accuracy"], 3) for m in b["trust"]["markets"]},
                          "by_regime": {r["name"]: round(r["accuracy"], 3) for r in b["trust"]["regimes"]}}
                         for b in ov["bots"] if b["role"] == "analyst"],
            "kill_switch": ov["halted"],
        }

    def _rule_answer(self, scope: list[str], readings: dict[str, list[dict]]) -> tuple[str, list[dict]]:
        now = now_ms()
        items = []
        for key in scope:
            sym = key.split(":", 1)[1]
            c = self.engine.council.consensus(key, now, self.engine.bar_ms.get(key, 60_000))
            rs = readings.get(key, [])
            bulls = [r for r in rs if r["direction"] > 0.15]
            bears = [r for r in rs if r["direction"] < -0.15]
            best = max(rs, key=lambda r: r["accuracy"] * abs(r["direction"]), default=None)
            lean = "bullish" if c.direction > 0.25 else "bearish" if c.direction < -0.25 else "split"
            text = f"{sym}: council is {lean} at {c.direction:+.2f}. {len(bulls)} analysts lean up, {len(bears)} down."
            if best and abs(best["direction"]) > 0.15:
                text += f" Most reliable voice: {best['analyst']} ({best['accuracy']:.0%} right) says \"{best['note']}\"."
            if c.risk_off > 0.3:
                text += f" Risk warnings at {c.risk_off:.0%}: traders size down."
            holders = [b.config["name"] for b in self.engine.bots.values()
                       if b.ledger and b.ledger.position(key).is_open]
            if holders:
                text += f" Holding it: {', '.join(holders)}."
            verb = "Watch" if lean != "split" else "Wait"
            items.append({"verb": verb, "text": text, "target": key})
        lead = "Here is what the analysts see right now." if items else "No analyst covers those markets yet."
        return lead, items

    def _trader_answer(self) -> tuple[str, list[dict]]:
        traders = sorted((b for b in self.engine.overview()["bots"] if b["role"] == "trader"),
                         key=lambda b: b["pnl_pct"], reverse=True)
        if not traders:
            return "There are no traders yet. Create one from the + button.", []
        total_budget = sum(b["budget"] for b in traders)
        total_equity = sum(b["equity"] for b in traders)
        items = []
        for b in traders:
            state = "paused" if b["paused_reason"] else "off" if not b["enabled"] else b["profile"]
            win = "no closed trades yet" if b["win_rate"] is None else f"{b['win_rate']:.0%} of {b['trades']} won"
            open_n = len(b["positions"])
            items.append({"verb": "Up" if b["pnl"] > 0 else "Down" if b["pnl"] < 0 else "Flat", "target": b["id"],
                          "text": f"{b['name']} ({b['title']}, {state}): {b['pnl_pct']:+.2%} on {b['budget']:g}, "
                                  f"{win}, {open_n} open position{'s' if open_n != 1 else ''}."})
        pnl = total_equity - total_budget
        return (f"{len(traders)} traders manage {total_budget:,.2f} in total, now worth {total_equity:,.2f} "
                f"({pnl:+,.2f}). Best first.", items)

    def _briefing_items(self) -> list[dict]:
        ov = self.engine.overview()
        items: list[dict] = []
        now = now_ms()

        hot = []
        for key, m in ov["markets"].items():
            if m["voices"] >= 3 and abs(m["direction"]) > 0.35:
                hot.append((abs(m["direction"]), key, m))
        if hot:
            _, key, m = max(hot)
            c = self.engine.council.consensus(key, now, self.engine.bar_ms.get(key, 60_000))
            agree = sum(1 for v in c.voices if v["kind"] != "risk" and v["direction"] * m["direction"] > 0)
            directional = sum(1 for v in c.voices if v["kind"] != "risk")
            side = "bullish" if m["direction"] > 0 else "bearish"
            items.append({"verb": "Watch", "target": key,
                          "text": f"{key.split(':', 1)[1]}: {agree} of {directional} analysts lean {side}, "
                                  f"weighted vote {m['direction']:+.2f}."})

        traders = [b for b in ov["bots"] if b["role"] == "trader"]
        paused = [b for b in traders if b["paused_reason"]]
        for b in paused[:1]:
            items.append({"verb": "Review", "target": b["id"],
                          "text": f"{b['name']} was paused by the risk manager: {b['paused_reason']}.",
                          "proposals": [{"id": new_id("prop"), "label": f"Resume {b['name']}",
                                         "action": {"type": "resume", "bot_id": b["id"]}}]})
        active = [b for b in traders if b["enabled"] and not b["paused_reason"]]
        weak = [b for b in active if b["trades"] >= 4 and b["pnl_pct"] < -0.02 and (b["win_rate"] or 0) < 0.4]
        if weak and not paused:
            b = min(weak, key=lambda x: x["pnl_pct"])
            items.append({"verb": "Review", "target": b["id"],
                          "text": f"{b['name']} is {b['pnl_pct']:+.1%} on its budget and won "
                                  f"{b['win_rate']:.0%} of {b['trades']} trades. Its setup is not working in this market.",
                          "proposals": [{"id": new_id("prop"), "label": f"Pause {b['name']}",
                                         "action": {"type": "pause", "bot_id": b["id"]}}]})
        strong = [b for b in active if b["trades"] >= 6 and b["pnl_pct"] > 0.02 and (b["win_rate"] or 0) >= 0.55]
        if strong:
            b = max(strong, key=lambda x: x["pnl_pct"])
            new_budget = round(b["budget"] * 1.25, 2)
            items.append({"verb": "Consider", "target": b["id"],
                          "text": f"{b['name']} leads at {b['pnl_pct']:+.1%} with {b['win_rate']:.0%} wins over "
                                  f"{b['trades']} trades. Past results do not guarantee the next ones.",
                          "proposals": [{"id": new_id("prop"), "label": f"Raise budget to {new_budget:g}",
                                         "action": {"type": "budget", "bot_id": b["id"], "value": new_budget}}]})

        risky = [(m["risk_off"], k) for k, m in ov["markets"].items() if m["risk_off"] > 0.5]
        if risky:
            level, key = max(risky)
            items.append({"verb": "Careful", "target": key,
                          "text": f"{key.split(':', 1)[1]} is turbulent, risk warnings at {level:.0%}. "
                                  "Traders are already sizing down there."})

        analysts = [b for b in ov["bots"] if b["role"] == "analyst" and b["reputation"]["calls"] >= 20]
        if analysts and len(items) < 3:
            b = max(analysts, key=lambda x: x["reputation"]["accuracy"])
            r = b["reputation"]
            items.append({"verb": "Note", "target": b["id"],
                          "text": f"Most reliable analyst so far: {b['name']}, right {r['accuracy']:.0%} "
                                  f"of {r['calls']} scored calls, vote weight {r['weight']:.2f}x."})
        return items[:4]
