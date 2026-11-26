"""Backtesting: replay history through the same council, trader and risk manager.

The replay uses the live components (analysts, council scoring, trader
strategy, risk manager, ledger), so a backtest tests the code that will
actually trade. Rules that keep it honest:

  * no look-ahead: decisions use bar i's close, orders fill at bar i+1's open;
  * every fill pays a taker fee and slippage;
  * stop losses are checked on each bar's low/high, not just the close;
  * analysts warm up before the trader is allowed to act.

A replay runs in two passes. The first records the council: what every
analyst said on every bar and the weighted vote that came out of it. The
second plays one trader against that record. Analysts never depend on the
trader, so walk-forward testing can try many trader settings against a
single recording.

It cannot model order-book depth, partial fills, outages or funding costs,
and a good result on one stretch of history says little about the next.
Analysts that need live exchange data (funding, open interest, the order
book) stay silent in a backtest.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import math
import time
from dataclasses import dataclass, field
from collections.abc import Callable

import numpy as np

from . import indicators as ta
from .bots import ANALYSTS, TRADERS, Consensus, PositionView, Series
from .learning import Council, regime_of
from .ledger import Ledger
from .models import Candle, Fill, Market, Mode, Side, new_id
from .risk import CORRELATED, PROFILES, RiskLimits, RiskManager, conviction

log = logging.getLogger(__name__)

FEE = 0.001
SLIPPAGE = 0.0005
WINDOW = 760  # history kept per bar, same as live; each strategy sees its own lookback of it
WARMUP = 80
MIN_SHARPE_DAYS = 30
MAX_BARS = 5000
CORR_EVERY = 10   # bars between correlation refreshes
MIN_FOLD_BARS = 150
FOLDS = 4

Progress = Callable[[float], None]


@dataclass
class BacktestSpec:
    strategy: str
    symbols: list[str]
    market: Market
    budget: float = 100.0
    params: dict[str, float] = field(default_factory=dict)
    limits: dict[str, float] = field(default_factory=dict)
    profile: str = "balanced"
    analysts: list[str] = field(default_factory=lambda: list(ANALYSTS))


@dataclass
class Tape:
    """Everything the council said on every bar of a replay."""
    ts: list[int]
    keys: list[str]
    arrays: dict[str, tuple[np.ndarray, ...]]   # open, high, low, close, volume per market
    votes: list[dict[str, Consensus]]           # index = bar; empty before the warmup ends
    regimes: list[dict[str, str]]
    corr: list[dict[tuple[str, str], float]]
    council: Council
    analysts: dict
    bar_ms: int

    def __len__(self) -> int:
        return len(self.ts)

    def series(self, key: str, i: int) -> Series:
        lo = max(0, i + 1 - WINDOW)
        _, high, low, close, volume = (a[lo:i + 1] for a in self.arrays[key])
        return Series(high, low, close, volume)


def _align(history: dict[str, list[Candle]]) -> tuple[list[int], dict[str, list[Candle]]]:
    """Keep only timestamps every symbol has, so multi-market traders see one clock."""
    common = set.intersection(*(set(c.ts for c in cs) for cs in history.values()))
    ts = sorted(common)
    aligned = {k: [c for c in cs if c.ts in common] for k, cs in history.items()}
    return ts, aligned


def record(spec: BacktestSpec, history: dict[str, list[Candle]], progress: Progress | None = None) -> Tape:
    """Pass one: replay the analysts and the council over the whole history."""
    ts, data = _align(history)
    n = len(ts)
    if n < WARMUP + 20:
        raise ValueError(f"Only {n} bars of shared history; need at least {WARMUP + 20}")
    analysts = {k: ANALYSTS[k]() for k in spec.analysts
                if k in ANALYSTS and spec.market.value in ANALYSTS[k].markets and not ANALYSTS[k].needs_extras}
    arrays = {k: tuple(np.array(x, dtype=float) for x in zip(*[(c.open, c.high, c.low, c.close, c.volume) for c in cs], strict=True))
              for k, cs in data.items()}
    tape = Tape(ts, list(data), arrays, [{} for _ in range(n)], [{} for _ in range(n)], [{} for _ in range(n)],
                Council(), analysts, ts[1] - ts[0])
    corr: dict[tuple[str, str], float] = {}
    for i in range(WARMUP, n):
        t = ts[i]
        series = {k: tape.series(k, i) for k in tape.keys}
        closes = {k: s.price for k, s in series.items()}
        regime = {k: regime_of(ta.efficiency_ratio(s.close, 30)) for k, s in series.items()}
        if (i - WARMUP) % CORR_EVERY == 0:
            corr = ta.correlations({k: s.close for k, s in series.items()})
        tape.council.score(closes, t)
        for aid, analyst in analysts.items():
            seen = {k: s.tail(analyst.lookback) for k, s in series.items()}
            for key, reading in analyst.read_all(seen).items():
                s = series[key].tail(220)
                atr_frac = float(ta.atr(s.high, s.low, s.close)[-1] / s.price)
                tape.council.hear(aid, key, reading, s.price, t, tape.bar_ms, atr_frac, regime[key])
        tape.votes[i] = {k: tape.council.consensus(k, t, tape.bar_ms, regime[k]) for k in tape.keys}
        tape.regimes[i] = regime
        tape.corr[i] = corr
        if progress and (i - WARMUP) % 50 == 0:
            progress((i - WARMUP) / (n - WARMUP))
    return tape


def simulate(spec: BacktestSpec, tape: Tape, start: int | None = None, end: int | None = None,
             params: dict[str, float] | None = None) -> dict:
    """Pass two: one trader with a fresh budget, trading bars [start, end) of the recording."""
    if spec.strategy not in TRADERS:
        raise ValueError(f"Unknown trader strategy: {spec.strategy}")
    start = max(WARMUP, start or WARMUP)
    end = min(len(tape), end or len(tape))
    trader = TRADERS[spec.strategy]({**spec.params, **(params or {})})
    profile = PROFILES.get(spec.profile, PROFILES["balanced"])
    limits = RiskLimits.from_dict({**profile["limits"].to_dict(), **spec.limits})
    size_mult = profile["size"]
    risk = RiskManager()
    ledger = Ledger(spec.budget)
    keys, arrays = tape.keys, tape.arrays

    equity_curve: list[tuple[int, float]] = []
    trades: list[dict] = []
    pending: list[tuple[str, Side, float, str]] = []  # orders decided on bar i, filled at bar i+1 open
    paused: str | None = None
    exposure_bars = 0
    day, day_start = None, spec.budget
    hold_qty = {k: (spec.budget / len(keys)) / arrays[k][3][start] for k in keys}

    def fill(key: str, side: Side, qty: float, price: float, reason: str, t: int) -> None:
        slip = SLIPPAGE if side == Side.BUY else -SLIPPAGE
        px = price * (1 + slip)
        f = Fill(new_id("bt"), "backtest", key, side, qty, px, qty * px * FEE, Mode.BACKTEST, reason, ts=t)
        f.pnl = ledger.apply(f)
        trades.append({"ts": t, "symbol": key, "side": side.value, "qty": qty, "price": px,
                       "fee": f.fee, "pnl": f.pnl, "reason": reason})

    def correlated(key: str, corr: dict, prices: dict[str, float]) -> float:
        return sum(abs(p.qty) * prices[o] * corr.get((key, o), 0.0) for o, p in ledger.positions.items()
                   if o != key and p.qty > 0 and corr.get((key, o), 0.0) >= CORRELATED)

    for i in range(start, end):
        t = tape.ts[i]
        opens = {k: arrays[k][0][i] for k in keys}

        # 1. Orders decided last bar fill at this bar's open.
        for key, side, qty, reason in pending:
            pos = ledger.position(key)
            if reason.startswith("exit:"):
                if pos.is_open:
                    fill(key, side, abs(pos.qty), opens[key], reason[5:], t)
                continue
            equity = ledger.equity(opens)
            verdict = risk.review(ledger, limits, spec.market, key, side, qty, opens[key], equity,
                                  correlated=correlated(key, tape.corr[i - 1], opens))
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

        # 4. The trader reads the closed bar and the council's vote on it.
        if paused or i == end - 1:
            continue
        for key in keys:
            s = tape.series(key, i).tail(trader.lookback)
            pos = ledger.position(key)
            vote = tape.votes[i][key]
            intent = trader.decide(s, PositionView(pos.qty, pos.avg_price), vote)
            if intent is None:
                continue
            if intent.exit:
                if pos.is_open:
                    pending.append((key, intent.side, abs(pos.qty), f"exit:{intent.reason}"))
            else:
                size = min(max(intent.size, 0) * size_mult * conviction(vote.direction), 1)
                pending.append((key, intent.side, equity * size / closes[key], intent.reason))

    # Mark open positions at the final close; report them rather than pretending they closed.
    final_close = {k: arrays[k][3][end - 1] for k in keys}
    hold_curve = [(t, sum(hold_qty[k] * arrays[k][3][start + j] for k in keys)) for j, (t, _) in enumerate(equity_curve)]
    result = _metrics(spec, equity_curve, hold_curve, trades, ledger.fees, ledger.equity(final_close),
                      exposure_bars, tape.bar_ms)
    result.update(
        paused=paused,
        bars=len(equity_curve),
        bar_ms=tape.bar_ms,
        start=equity_curve[0][0],
        end=equity_curve[-1][0],
        params=trader.p,
        open_positions={k: {"qty": p.qty, "avg_price": p.avg_price, "price": final_close[k]}
                        for k, p in ledger.positions.items() if p.is_open},
    )
    return result


def _analyst_report(tape: Tape) -> list[dict]:
    return [{"key": aid, "title": a.title, "persona": a.persona, **tape.council.rep(aid).to_dict()}
            for aid, a in tape.analysts.items()]


def run_backtest(spec: BacktestSpec, history: dict[str, list[Candle]], progress: Progress | None = None) -> dict:
    started = time.perf_counter()
    tape = record(spec, history, (lambda p: progress(p * 0.95)) if progress else None)
    result = simulate(spec, tape)
    result.update(analysts=_analyst_report(tape), seconds=round(time.perf_counter() - started, 2))
    if progress:
        progress(1.0)
    return result


def _grid(strategy: str) -> list[dict[str, float]]:
    tunable = TRADERS[strategy].tunable
    if not tunable:
        return [{}]
    names = list(tunable)
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*(tunable[n] for n in names))]


def _score(r: dict) -> float:
    """What tuning optimises: return, with drawdown counted against it."""
    return r["return"] + 0.5 * r["max_drawdown"]


def run_walk_forward(spec: BacktestSpec, history: dict[str, list[Candle]], progress: Progress | None = None,
                     folds: int = FOLDS) -> dict:
    """Tune on one stretch, trade the next with what was picked, roll forward. Only the unseen stretches count."""
    started = time.perf_counter()
    tape = record(spec, history, (lambda p: progress(p * 0.7)) if progress else None)
    usable = len(tape) - WARMUP
    chunk = usable // (folds + 1)
    if chunk < MIN_FOLD_BARS:
        raise ValueError(f"Walk-forward needs at least {MIN_FOLD_BARS * (folds + 1) + WARMUP} bars; "
                         f"this history has {len(tape)}. Pick a longer history")
    bounds = [WARMUP + chunk * f for f in range(folds + 1)] + [len(tape)]
    grid = _grid(spec.strategy)
    rows, tests = [], []
    for f in range(1, folds + 1):
        train = (bounds[f - 1], bounds[f])
        test = (bounds[f], bounds[f + 1])
        tried = [(simulate(spec, tape, *train, params=p), p) for p in grid]
        best, picked = max(tried, key=lambda x: _score(x[0]))
        out = simulate(spec, tape, *test, params=picked)
        baseline = simulate(spec, tape, *test)
        tests.append(out)
        rows.append({"train_start": tape.ts[train[0]], "test_start": tape.ts[test[0]], "test_end": tape.ts[test[1] - 1],
                     "params": picked, "train_return": best["return"], "test_return": out["return"],
                     "default_return": baseline["return"], "test_buy_hold": out["buy_hold_return"],
                     "test_drawdown": out["max_drawdown"], "trades": out["trades"]})
        if progress:
            progress(0.7 + 0.3 * f / folds)

    # Stitch the unseen stretches into one account: each picks up where the last one ended.
    curve, hold, trades = [], [], []
    equity, held = spec.budget, spec.budget
    for out in tests:
        scale, hscale = equity / spec.budget, held / spec.budget
        curve += [[t, v * scale] for t, v in out["equity_curve"]]
        hold += [[t, v * hscale] for t, v in out["hold_curve"]]
        trades += [{**t, "pnl": t["pnl"] * scale} for t in out["trade_log"]]
        equity, held = out["final_equity"] * scale, out["hold_final"] * hscale
    values = np.array([v for _, v in curve])
    peak = np.maximum.accumulate(values)
    closed = [t for t in trades if t["pnl"] != 0]
    gains = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    in_sample = float(np.mean([r["train_return"] for r in rows]))
    out_sample = float(np.mean([r["test_return"] for r in rows]))
    result = {
        "strategy": spec.strategy, "title": TRADERS[spec.strategy].title, "persona": TRADERS[spec.strategy].persona,
        "symbols": spec.symbols, "budget": spec.budget, "final_equity": equity,
        "return": equity / spec.budget - 1, "buy_hold_return": held / spec.budget - 1,
        "max_drawdown": float(((values - peak) / peak).min()), "sharpe": None,
        "trades": sum(o["trades"] for o in tests), "closed_trades": len(closed),
        "win_rate": (sum(1 for t in closed if t["pnl"] > 0) / len(closed)) if closed else None,
        "profit_factor": (gains / losses) if losses > 0 else None,
        "fees": sum(o["fees"] for o in tests),
        "exposure": float(np.mean([o["exposure"] for o in tests])),
        "equity_curve": curve, "hold_curve": hold, "trade_log": trades[-200:],
        "paused": next((o["paused"] for o in tests if o["paused"]), None),
        "bars": sum(o["bars"] for o in tests), "bar_ms": tape.bar_ms,
        "start": curve[0][0], "end": curve[-1][0], "open_positions": tests[-1]["open_positions"],
        "analysts": _analyst_report(tape), "seconds": round(time.perf_counter() - started, 2),
        "walk_forward": {"folds": rows, "settings_tried": len(grid), "in_sample": in_sample,
                         "out_of_sample": out_sample, "tunable": list(TRADERS[spec.strategy].tunable)},
    }
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
        walk = bool(data.get("walk_forward"))
        if walk and bars < MIN_FOLD_BARS * (FOLDS + 1) + WARMUP:
            raise ConfigError(f"Walk-forward needs at least {MIN_FOLD_BARS * (FOLDS + 1) + WARMUP} bars of history")
        budget = float(data.get("budget") or 100)
        if budget <= 0:
            raise ConfigError("Budget must be above zero")
        spec = BacktestSpec(strategy, symbols, acc.market, budget,
                            {k: float(v) for k, v in (data.get("params") or {}).items()},
                            {k: float(v) for k, v in (data.get("limits") or {}).items()},
                            profile=data.get("profile") if data.get("profile") in PROFILES else "balanced")
        run = {"id": new_id("bt"), "status": "queued", "progress": 0.0, "created": int(time.time() * 1000),
               "strategy": strategy, "title": TRADERS[strategy].title, "persona": TRADERS[strategy].persona,
               "account": acc.record["name"], "symbols": symbols, "bars": bars, "budget": budget,
               "profile": spec.profile, "walk_forward": walk,
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

                job = run_walk_forward if run["walk_forward"] else run_backtest
                run["result"] = await asyncio.to_thread(job, spec, history, progress)
                run["status"] = "done"
                run["summary"] = {k: run["result"][k] for k in
                                  ("return", "buy_hold_return", "max_drawdown", "trades", "win_rate", "sharpe")}
            except Exception as exc:  # surface any failure to the user instead of a silent dead job
                log.exception("Backtest %s failed", run["id"])
                run["status"], run["error"] = "failed", str(exc)
            self._emit(run)


def _metrics(spec: BacktestSpec, curve, hold_curve, trades, fees: float, final_equity: float,
             exposure_bars: int, bar_ms: int) -> dict:
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
    step = max(1, len(curve) // 400)  # keep the payload small; the chart does not need every bar
    return {
        "strategy": spec.strategy,
        "title": TRADERS[spec.strategy].title,
        "persona": TRADERS[spec.strategy].persona,
        "symbols": spec.symbols,
        "budget": spec.budget,
        "final_equity": final_equity,
        "hold_final": float(hold[-1]),
        "return": final_equity / spec.budget - 1,
        "buy_hold_return": float(hold[-1] / spec.budget - 1),
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate": (sum(1 for t in closed if t["pnl"] > 0) / len(closed)) if closed else None,
        "profit_factor": (gains / losses) if losses > 0 else None,
        "fees": fees,
        "exposure": exposure_bars / len(curve),
        "equity_curve": [[int(t), round(float(v), 4)] for t, v in curve[::step]],
        "hold_curve": [[int(t), round(float(v), 4)] for t, v in hold_curve[::step]],
        "trade_log": trades[-200:],
    }
