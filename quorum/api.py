"""HTTP + WebSocket API and static dashboard.

Binds to localhost by default: the dashboard controls real money, so it
must never be exposed to a network without the user deciding to.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .backtest import BacktestRunner
from .bots import catalog
from .bus import Bus
from .connectors import VENUES
from .engine import ConfigError, Engine
from .lead import Lead
from .security import Vault
from .storage import Store

log = logging.getLogger(__name__)

WEB = Path(__file__).parent / "web"
def _default_data_dir() -> Path:
    """`./data` when running from a checkout, `~/.quorum` when installed with pip."""
    repo = Path(__file__).resolve().parents[1]
    return repo / "data" if (repo / "pyproject.toml").exists() else Path.home() / ".quorum"


DATA = Path(os.environ.get("QUORUM_DATA") or _default_data_dir())


def create_app() -> FastAPI:
    bus = Bus(history=3000)
    store, vault = Store(DATA / "quorum.db"), Vault(DATA)
    engine = Engine(store, vault, bus)
    lead = Lead(engine, store, vault, bus)
    backtests = BacktestRunner(engine, bus)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        await lead.start()
        yield
        await lead.stop()
        await engine.stop()

    app = FastAPI(title="Quorum", lifespan=lifespan, docs_url="/api/docs")

    @app.middleware("http")
    async def revalidate_assets(request, call_next):
        # The dashboard ships with the package; after an update the browser must not run stale JS.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def guard(fn, *args: Any):
        try:
            return fn(*args)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, f"Not found: {exc}") from exc

    async def aguard(coro):
        try:
            return await coro
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, f"Not found: {exc}") from exc

    @app.get("/api/state")
    def state() -> dict:
        return {**engine.overview(), "setup": lead.onboarding.summary()}

    @app.post("/api/council")
    async def council(data: dict = Body(...)) -> dict:
        """Start or stop the whole council from the UI; mirrors the Chair's start/stop commands."""
        if data.get("running"):
            await aguard(lead.ask("start"))
        else:
            await aguard(lead.ask("stop"))
        return {"running": engine.running}

    @app.get("/api/catalog")
    def strategies() -> dict:
        return {"strategies": catalog(), "venues": VENUES}

    @app.get("/api/messages")
    def messages(limit: int = 200) -> list[dict]:
        return list(bus.history)[-limit:]

    @app.get("/api/bots/{bot_id}")
    def bot(bot_id: str) -> dict:
        b = guard(engine.bots.__getitem__, bot_id)
        return {
            **engine.bot_view(b),
            "equity_curve": engine.store.equity(bot_id),
            "fills": engine.store.fills(bot_id, 60),
            "messages": [m for m in bus.history if m["data"].get("source") == bot_id
                         or m["data"].get("bot") == bot_id or m["data"].get("bot_id") == bot_id][-60:],
        }

    @app.post("/api/bots")
    def create_bot(data: dict = Body(...)) -> dict:
        return engine.bot_view(guard(engine.create_bot, data))

    @app.patch("/api/bots/{bot_id}")
    def update_bot(bot_id: str, data: dict = Body(...)) -> dict:
        return engine.bot_view(guard(engine.update_bot, bot_id, data))

    @app.post("/api/bots/{bot_id}/close")
    async def close_bot(bot_id: str) -> dict:
        await aguard(engine.close_positions(bot_id))
        return engine.bot_view(engine.bots[bot_id])

    @app.delete("/api/bots/{bot_id}")
    def delete_bot(bot_id: str) -> dict:
        guard(engine.delete_bot, bot_id)
        return {"ok": True}

    @app.post("/api/accounts")
    async def add_account(data: dict = Body(...)) -> dict:
        acc = await aguard(engine.add_account(data))
        return acc.public()

    @app.post("/api/accounts/{account_id}/check")
    async def check_account(account_id: str) -> dict:
        await aguard(engine.check_account(account_id))
        return engine.accounts[account_id].public()

    @app.delete("/api/accounts/{account_id}")
    async def remove_account(account_id: str) -> dict:
        await aguard(engine.remove_account(account_id))
        return {"ok": True}

    @app.post("/api/halt")
    async def halt(data: dict = Body(...)) -> dict:
        await engine.halt(bool(data.get("on")), bool(data.get("flatten")))
        return {"halted": engine.risk.halted}

    @app.get("/api/chat")
    def chat(limit: int = 120) -> list[dict]:
        return lead.history(limit)

    @app.post("/api/chat")
    async def ask(data: dict = Body(...)) -> dict:
        text = str(data.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "Write a message for the Chair")
        await aguard(lead.ask(text))
        return {"ok": True}

    @app.post("/api/chat/{msg_id}/answer")
    async def answer(msg_id: str, data: dict = Body(...)) -> dict:
        return await aguard(lead.answer_prompt(msg_id, data.get("value") or {}))

    @app.post("/api/chat/brief")
    async def brief() -> dict:
        msg = await lead.brief(force=True)
        if msg is None:
            raise HTTPException(409, "Nothing to report yet. The analysts need a few bars first.")
        return msg

    @app.post("/api/chat/{msg_id}/proposals/{proposal_id}")
    def apply_proposal(msg_id: str, proposal_id: str) -> dict:
        return guard(lead.apply_proposal, msg_id, proposal_id)

    @app.post("/api/backtests")
    async def start_backtest(data: dict = Body(...)) -> dict:
        # async on purpose: the runner schedules its job on the event loop, which threadpool endpoints lack
        return guard(backtests.start, data)

    @app.get("/api/backtests")
    def list_backtests() -> list[dict]:
        runs = sorted(backtests.runs.values(), key=lambda r: r["created"], reverse=True)
        return [{k: v for k, v in r.items() if k != "result"} for r in runs]

    @app.get("/api/backtests/{run_id}")
    def get_backtest(run_id: str) -> dict:
        return guard(backtests.runs.__getitem__, run_id)

    @app.get("/api/settings")
    def get_settings() -> dict:
        return lead.settings()

    @app.post("/api/settings")
    def set_settings(data: dict = Body(...)) -> dict:
        return lead.update_settings(data)

    @app.get("/api/equity")
    def equity(bot_id: str = "__all__") -> list:
        return engine.store.equity(bot_id)

    @app.get("/api/series")
    def series(key: str) -> list[float]:
        return engine.candles(key)

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        q = bus.stream()
        try:
            while True:
                msg = await q.get()
                await sock.send_json(msg)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            log.debug("WebSocket closed", exc_info=True)
        finally:
            bus.unstream(q)

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index() -> HTMLResponse:
        # Fingerprint asset URLs so a browser never keeps running an old dashboard after an update.
        stamp = int(max((WEB / f).stat().st_mtime for f in ("app.css", "app.js")))
        html = (WEB / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("/static/app.css", f"/static/app.css?v={stamp}")
                                .replace("/static/app.js", f"/static/app.js?v={stamp}"))

    return app
