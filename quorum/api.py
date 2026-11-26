"""HTTP + WebSocket API and static dashboard.

Binds to localhost by default: the dashboard controls real money, so it
must never be exposed to a network without the user deciding to.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import COOKIE, LOGIN_PAGE, MAX_AGE, OPEN_PATHS, Gate
from .backtest import BacktestRunner
from .bots import catalog
from .bus import Bus
from .connectors import VENUES
from .engine import ConfigError, Engine
from .lead import Lead
from .notify import Notifier
from .plugins import load_plugins
from .risk import PROFILES
from .security import Vault
from .storage import Store

log = logging.getLogger(__name__)

WEB = Path(__file__).parent / "web"
def _default_data_dir() -> Path:
    """`./data` when running from a checkout, `~/.quorum` when installed with pip."""
    repo = Path(__file__).resolve().parents[1]
    return repo / "data" if (repo / "pyproject.toml").exists() else Path.home() / ".quorum"


DATA = Path(os.environ.get("QUORUM_DATA") or _default_data_dir())


def _default_strategies_dir() -> Path:
    """`./strategies` in a checkout, `<data>/strategies` when installed with pip."""
    repo = Path(__file__).resolve().parents[1]
    return repo / "strategies" if (repo / "pyproject.toml").exists() else DATA / "strategies"


STRATEGIES_DIR = Path(os.environ.get("QUORUM_STRATEGIES") or _default_strategies_dir())

CSV_FIELDS = ["time_utc", "trader", "account", "market", "side", "quantity", "price", "value", "fee", "result",
              "reason"]


def create_app(data_dir: Path | None = None, strategies_dir: Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or DATA)
    strategies_dir = Path(strategies_dir or STRATEGIES_DIR)
    plugins, plugin_errors = load_plugins(strategies_dir)  # before the engine mounts bots that may use them
    bus = Bus(history=3000)
    store, vault = Store(data_dir / "quorum.db"), Vault(data_dir)
    gate = Gate(data_dir)
    engine = Engine(store, vault, bus)
    lead = Lead(engine, store, vault, bus)
    notifier = Notifier(engine, lead, store, vault, bus)
    backtests = BacktestRunner(engine, bus)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        await lead.start()
        await notifier.start()
        yield
        await notifier.stop()
        await lead.stop()
        await engine.stop()

    app = FastAPI(title="Quorum", lifespan=lifespan, docs_url="/api/docs")

    @app.middleware("http")
    async def revalidate_assets(request, call_next):
        if gate.enabled and request.url.path not in OPEN_PATHS and not gate.allows(request.cookies.get(COOKIE)):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Sign in first"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
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

    @app.get("/login")
    def login_page() -> HTMLResponse:
        if not gate.enabled:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(LOGIN_PAGE.format(error=""))

    @app.post("/login")
    async def login(request: Request) -> Response:
        attempt = parse_qs((await request.body()).decode())
        if gate.enabled and await gate.check_password((attempt.get("password") or [""])[0]):
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE, gate.token(), max_age=MAX_AGE, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https")
            return resp
        return HTMLResponse(LOGIN_PAGE.format(error='<div class="hint bad">Wrong password.</div>'), status_code=401)

    @app.get("/logout")
    def logout() -> Response:
        resp = RedirectResponse("/login" if gate.enabled else "/", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/api/state")
    def state() -> dict:
        return {**engine.overview(), "setup": lead.onboarding.summary(), "auth": gate.enabled,
                "plugins": {"loaded": plugins, "errors": plugin_errors, "folder": str(strategies_dir)}}

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
        profiles = {k: {"label": p["label"], "hint": p["hint"], "size": p["size"], "limits": p["limits"].to_dict()}
                    for k, p in PROFILES.items()}
        return {"strategies": catalog(), "venues": VENUES, "profiles": profiles}

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
        return {**lead.settings(), "exchange_stops": engine.exchange_stops}

    @app.post("/api/settings")
    async def set_settings(data: dict = Body(...)) -> dict:
        if "exchange_stops" in data:
            await engine.set_exchange_stops(bool(data["exchange_stops"]))
        return {**lead.update_settings(data), "exchange_stops": engine.exchange_stops}

    @app.get("/api/notify")
    def notify_settings() -> dict:
        return notifier.settings()

    @app.post("/api/notify")
    async def update_notify(data: dict = Body(...)) -> dict:  # async: a new Telegram token starts the poller
        return guard(notifier.update, data)

    @app.post("/api/notify/test")
    async def test_notify() -> dict:
        try:
            await aguard(notifier.send_now("Quorum is connected. You will hear from the team here."))
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/export/trades.csv")
    def export_trades(bot_id: str | None = None) -> Response:
        """Every fill, oldest first, for spreadsheets and tax tools."""
        names = {a.id: a.record["name"] for a in engine.accounts.values()}
        out = io.StringIO()
        writer = csv.DictWriter(out, CSV_FIELDS)
        writer.writeheader()
        for f in reversed(store.fills(bot_id, limit=1_000_000)):
            account_id, symbol = f["instrument_key"].split(":", 1)
            writer.writerow({
                "time_utc": datetime.fromtimestamp(f["ts"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "trader": f.get("bot_name", f["bot_id"]), "account": names.get(account_id, account_id),
                "market": symbol, "side": f["side"], "quantity": f"{f['qty']:.10g}", "price": f"{f['price']:.10g}",
                "value": f"{f['qty'] * f['price']:.2f}", "fee": f"{f['fee']:.6f}", "result": f"{f.get('pnl', 0):.6f}",
                "reason": f["reason"]})
        stamp = datetime.now().strftime("%Y-%m-%d")
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="quorum-trades-{stamp}.csv"'})

    @app.get("/api/equity")
    def equity(bot_id: str = "__all__") -> list:
        return engine.store.equity(bot_id)

    @app.get("/api/series")
    def series(key: str) -> list[float]:
        return engine.candles(key)

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        if not gate.allows(sock.cookies.get(COOKIE)):
            await sock.close(code=4401)
            return
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
