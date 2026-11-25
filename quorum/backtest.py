"""Backtesting: replay history through the same council, trader and risk manager.

The replay uses the live components (analysts, council scoring, trader
strategy, risk manager, ledger), so a backtest tests the code that will
actually trade. Rules that keep it honest:

  * no look-ahead: decisions use bar i's close, orders fill at bar i+1's open;
  * every fill pays the paper fee and slippage;
  * stop losses are checked on each bar's low/high, not just the close;
  * analysts warm up before the trader is allowed to act.

It cannot model order-book depth, partial fills, outages or funding costs,
and a good result on one stretch of history says little about the next.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import indicators as ta
from .bots import ANALYSTS, TRADERS, PositionView, Series
from .learning import Council
from .ledger import Ledger
from .models import Candle, Fill, Market, Mode, Side, new_id
from .risk import RiskLimits, RiskManager

log = logging.getLogger(__name__)

FEE = 0.001
SLIPPAGE = 0.0005
WINDOW = 220  # bars each strategy sees, same as live
WARMUP = 80
MIN_SHARPE_DAYS = 30
MAX_BARS = 5000

Progress = Callable[[float], None]


@dataclass
class BacktestSpec:
    strategy: str
    symbols: list[str]
    market: Market
    budget: float = 100.0
    params: dict[str, float] = field(default_factory=dict)
    limits: dict[str, float] = field(default_factory=dict)
    analysts: list[str] = field(default_factory=lambda: list(ANALYSTS))


def _align(history: dict[str, list[Candle]]) -> tuple[list[int], dict[str, list[Candle]]]:
    """Keep only timestamps every symbol has, so multi-market traders see one clock."""
    common = set.intersection(*(set(c.ts for c in cs) for cs in history.values()))
    ts = sorted(common)
    aligned = {k: [c for c in cs if c.ts in common] for k, cs in history.items()}
    return ts, aligned


def run_backtest(spec: BacktestSpec, history: dict[str, list[Candle]], progress: Progress | None = None) -> dict:
    if spec.strategy not in TRADERS:
        raise ValueError(f"Unknown trader strategy: {spec.strategy}")
    ts, data = _align(history)
    n = len(ts)
    if n < WARMUP + 20:
        raise ValueError(f"Only {n} bars of shared history; need at least {WARMUP + 20}")

    started = time.perf_counter()
    trader = TRADERS[spec.strategy](spec.params)
    analysts = {k: ANALYSTS[k]() for k in spec.analysts if k in ANALYSTS}
    limits = RiskLimits.from_dict(spec.limits)
    risk = RiskManager()
    council = Council()
    ledger = Ledger(spec.budget)
    bar_ms = ts[1] - ts[0]

    arrays = {k: tuple(np.array(x, dtype=float) for x in zip(*[(c.open, c.high, c.low, c.close, c.volume) for c in cs]))
              for k, cs in data.items()}
    keys = list(data)

    equity_curve: list[tuple[int, float]] = []
    trades: list[dict] = []
    pending: list[tuple[str, Side, float, str]] = []  # orders decided on bar i, filled at bar i+1 open
    paused: str | None = None
    exposure_bars = 0
    day, day_start = None, spec.budget
    hold_qty = {k: (spec.budget / len(keys)) / arrays[k][3][WARMUP] for k in keys}

    def fill(key: str, side: Side, qty: float, price: float, reason: str, t: int) -> None:
        slip = SLIPPAGE if side == Side.BUY else -SLIPPAGE
        px = price * (1 + slip)
        f = Fill(new_id("bt"), "backtest", key, side, qty, px, qty * px * FEE, Mode.PAPER, reason, ts=t)
        f.pnl = ledger.apply(f)
        trades.append({"ts": t, "symbol": key, "side": side.value, "qty": qty, "price": px,
                       "fee": f.fee, "pnl": f.pnl, "reason": reason})

    for i in range(WARMUP, n):
        t = ts[i]
        opens = {k: arrays[k][0][i] for k in keys}

        # 1. Orders decided last bar fill at this bar's open.
        for key, side, qty, reason in pending:
            pos = ledger.position(key)
            if reason.startswith("exit:"):
                if pos.is_open:
                    fill(key, side, abs(pos.qty), opens[key], reason[5:], t)
                continue
            equity = ledger.equity(opens)
            verdict = risk.review(ledger, limits, spec.market, key, side, qty, opens[key], equity)
            if verdict.approved:
                fill(key, side, verdict.qty, opens[key], reason, t)
        pending = []

        closes = {k: arrays[k][3][i] for k in keys}

        # 2. Intrabar stop losses, using the bar's worst price.
        for key in keys:
            pos = ledger.position(key)
            if not pos.is_open:
                continue
            worst = arrays[key][2][i] if pos.qty > 0 else arrays[key][1][i]
            hit = risk.stop_triggered(ledger, limits, key, worst)
            if hit:
                stop_px = pos.avg_price * (1 - limits.stop_loss_pct) if pos.qty > 0 else pos.avg_price * (1 + limits.stop_loss_pct)
                px = min(stop_px, opens[key]) if pos.qty > 0 else max(stop_px, opens[key])  # gaps fill worse
                fill(key, Side.SELL if pos.qty > 0 else Side.BUY, abs(pos.qty), px, hit, t)

        equity = ledger.equity(closes)
        equity_curve.append((t, equity))
        exposure_bars += int(any(p.is_open for p in ledger.positions.values()))

        # 3. Daily and drawdown limits, same rules as live.
        today = t // 86_400_000
        if today != day:
            day, day_start = today, equity
            if paused and paused.startswith("Daily"):
                paused = None
        if not paused:
            breach = risk.check_bot(ledger, limits, equity, day_start)
            if breach:
                paused = breach[1]
                for key in keys:
                    pos = ledger.position(key)
                    if pos.is_open:
                        pending.append((key, Side.SELL if pos.qty > 0 else Side.BUY, abs(pos.qty), f"exit:{paused}"))

        # 4. Analysts read the closed bar, the council learns, the trader decides.
        series = {}
        for k in keys:
            lo = max(0, i + 1 - WINDOW)
            _, high, low, close, volume = (a[lo:i + 1] for a in arrays[k])
            series[k] = Series(high, low, close, volume)
        council.score(closes, t)
        for aid, analyst in analysts.items():
            for key, reading in analyst.read_all(series).items():
                s = series[key]
                atr_frac = float(ta.atr(s.high, s.low, s.close)[-1] / s.price)
                council.hear(aid, key, reading, s.price, t, bar_ms, atr_frac)

        if paused or i == n - 1:
            continue
        for key in keys:
            s = series[key]
            pos = ledger.position(key)
            intent = trader.decide(s, PositionView(pos.qty, pos.avg_price), council.consensus(key, t, bar_ms))
            if intent is None:
                continue
            if intent.exit:
                if pos.is_open:
                    pending.append((key, intent.side, abs(pos.qty), f"exit:{intent.reason}"))
            else:
                qty = equity * min(max(intent.size, 0), 1) / closes[key]
                pending.append((key, intent.side, qty, intent.reason))

        if progress and (i - WARMUP) % 50 == 0:
            progress((i - WARMUP) / (n - WARMUP))

    # Mark open positions at the final close; report them rather than pretending they closed.
    final_close = {k: arrays[k][3][-1] for k in keys}
    hold_curve = [(t, sum(hold_qty[k] * arrays[k][3][WARMUP + j] for k in keys)) for j, (t, _) in enumerate(equity_curve)]
    result = _metrics(spec, equity_curve, hold_curve, trades, ledger, final_close, exposure_bars, bar_ms)
    result.update(
        paused=paused,
        bars=len(equity_curve),
        bar_ms=bar_ms,
        start=equity_curve[0][0],
        end=equity_curve[-1][0],
        open_positions={k: {"qty": p.qty, "avg_price": p.avg_price, "price": final_close[k]}
                        for k, p in ledger.positions.items() if p.is_open},
        analysts=[{"key": aid, "title": a.title, "persona": a.persona, **council.rep(aid).to_dict()}
                  for aid, a in analysts.items()],
        seconds=round(time.perf_counter() - started, 2),
    )
    if progress:
        progress(1.0)
    return result


class BacktestRunner:
    """Runs one backtest at a time off the event loop and streams progress over the bus."""

    def __init__(self, engine, bus, keep: int = 20) -> None:
        self.engine, self.bus, self.keep = engine, bus, keep
        self.runs: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._jobs: set[asyncio.Task] = set()

    def start(self, data: dict) -> dict:
        from .engine import ConfigError  # local import: engine imports nothing from here

        strategy = data.get("strategy")
        if strategy not in TRADERS:
            raise ConfigError("Pick a trader strategy to test")
        acc = self.engine.accounts.get(data.get("account_id"))
        if acc is None:
            raise ConfigError("Pick an account to take history from")
        symbols = [s for s in data.get("symbols", []) if s in acc.record["symbols"]]
        if not symbols:
            raise ConfigError("Pick at least one market")
        bars = int(data.get("bars") or 1000)
        if not WARMUP + 20 <= bars <= MAX_BARS:
            raise ConfigError(f"History length must be between {WARMUP + 20} and {MAX_BARS} bars")
        budget = float(data.get("budget") or 100)
        if budget <= 0:
            raise ConfigError("Budget must be above zero")
        spec = BacktestSpec(strategy, symbols, acc.market, budget,
                            {k: float(v) for k, v in (data.get("params") or {}).items()},
                            {k: float(v) for k, v in (data.get("limits") or {}).items()})
        run = {"id": new_id("bt"), "status": "queued", "progress": 0.0, "created": int(time.time() * 1000),
               "strategy": strategy, "title": TRADERS[strategy].title, "persona": TRADERS[strategy].persona,
               "account": acc.record["name"], "symbols": symbols, "bars": bars, "budget": budget,
               "bot_id": data.get("bot_id")}
        self.runs[run["id"]] = run
        for old in sorted(self.runs.values(), key=lambda r: r["created"])[:-self.keep]:
            self.runs.pop(old["id"], None)
        job = asyncio.create_task(self._run(run, spec, acc.connector))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        return run

    def _emit(self, run: dict) -> None:
        self.bus.publish("backtest", {k: v for k, v in run.items() if k != "result"}, keep=False)

    async def _run(self, run: dict, spec: BacktestSpec, connector) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            try:
                run["status"] = "loading"
                self._emit(run)
                history = {s: await connector.history(s, run["bars"]) for s in spec.symbols}
                run["status"] = "running"
                self._emit(run)

                def progress(p: float) -> None:
                    run["progress"] = round(p, 3)
                    loop.call_soon_threadsafe(self._emit, run)

                run["result"] = await asyncio.to_thread(run_backtest, spec, history, progress)
                run["status"] = "done"
                run["summary"] = {k: run["result"][k] for k in
                                  ("return", "buy_hold_return", "max_drawdown", "trades", "win_rate", "sharpe")}
            except Exception as exc:  # surface any failure to the user instead of a silent dead job
                log.exception("Backtest %s failed", run["id"])
                run["status"], run["error"] = "failed", str(exc)
            self._emit(run)


def _metrics(spec: BacktestSpec, curve, hold_curve, trades, ledger: Ledger, final_close, exposure_bars, bar_ms) -> dict:
    values = np.array([v for _, v in curve])
    hold = np.array([v for _, v in hold_curve])
    rets = np.diff(values) / values[:-1]
    bars_per_year = 365 * 24 * 3600 * 1000 / bar_ms
    span_days = (curve[-1][0] - curve[0][0]) / 86_400_000
    # Annualising a few hours of returns produces absurd numbers; only report Sharpe on a month or more.
    sharpe = (float(rets.mean() / rets.std() * math.sqrt(bars_per_year))
              if span_days >= MIN_SHARPE_DAYS and len(rets) > 1 and rets.std() > 0 else None)
    peak = np.maximum.accumulate(values)
    max_dd = float(((values - peak) / peak).min())
    closed = [t for t in trades if t["pnl"] != 0]
    gains = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    final_equity = ledger.equity(final_close)
    step = max(1, len(curve) // 400)  # keep the payload small; the chart does not need every bar
    return {
        "strategy": spec.strategy,
        "title": TRADERS[spec.strategy].title,
        "persona": TRADERS[spec.strategy].persona,
        "symbols": spec.symbols,
        "budget": spec.budget,
        "final_equity": final_equity,
        "return": final_equity / spec.budget - 1,
        "buy_hold_return": float(hold[-1] / spec.budget - 1),
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate": (sum(1 for t in closed if t["pnl"] > 0) / len(closed)) if closed else None,
        "profit_factor": (gains / losses) if losses > 0 else None,
        "fees": ledger.fees,
        "exposure": exposure_bars / len(curve),
        "equity_curve": [[int(t), round(float(v), 4)] for t, v in curve[::step]],
        "hold_curve": [[int(t), round(float(v), 4)] for t, v in hold_curve[::step]],
        "trade_log": trades[-200:],
    }
