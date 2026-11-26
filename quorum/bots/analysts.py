"""Analysts: they never trade, they only tell the council what they see."""
from __future__ import annotations

import numpy as np

from .. import indicators as ta
from .base import Analyst, Reading, Series


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, x)))


def price_str(v: float) -> str:
    return f"{v:,.2f}" if v >= 100 else f"{v:,.4f}" if v >= 1 else f"{v:.6f}"


class TrendAnalyst(Analyst):
    key = "trend"
    title = "Trend"
    persona = "Vector"
    summary = ("Reads the trend on two charts at once: the one you trade and one twelve times longer. "
               "Speaks up when both agree and warns when they don't.")
    defaults = {"fast": 20, "slow": 50, "zoom": 12}
    lookback = 720  # enough history to rebuild a 12x longer chart

    def read(self, s: Series) -> Reading:
        short = s.tail(220)
        fast = ta.ema(short.close, int(self.p["fast"]))
        slow = ta.ema(short.close, int(self.p["slow"]))
        vol = ta.atr(short.high, short.low, short.close)[-1] or 1e-9
        spread = (fast[-1] - slow[-1]) / vol
        slope = (slow[-1] - slow[-6]) / vol
        local = _clip(np.tanh(spread / 2) * 0.7 + np.tanh(slope) * 0.3)
        word = lambda d: "up" if d > 0.15 else "down" if d < -0.15 else "sideways"  # noqa: E731

        big = self._higher_timeframe(s.close)
        if big is None:  # not enough history yet: judge on the short chart alone
            return Reading("trend", local, _clip(abs(local), 0, 1),
                           f"Trend {word(local)}: fast EMA {spread:+.1f} ATR from slow")
        zoom = int(self.p["zoom"])
        if local * big > 0:
            direction = _clip(local * 0.6 + big * 0.4)
            return Reading("trend", direction, _clip(abs(direction) + 0.15, 0, 1),
                           f"Trend {word(direction)} on both the short chart and the {zoom}x chart")
        direction = _clip(local * 0.5 + big * 0.5)
        return Reading("trend", direction, _clip(abs(direction) * 0.7, 0, 1),
                       f"Short chart {word(local)}, {zoom}x chart {word(big)}: trading against the bigger trend")

    def _higher_timeframe(self, close: np.ndarray) -> float | None:
        """Trend direction on a chart `zoom` times longer, rebuilt by sampling every zoom-th close."""
        zoom = int(self.p["zoom"])
        sampled = close[::-1][::zoom][::-1]
        if len(sampled) < 40:
            return None
        fast, slow = ta.ema(sampled, 9)[-1], ta.ema(sampled, 26)[-1]
        swing = np.std(np.diff(np.log(sampled))) * sampled[-1] or 1e-9
        return _clip(float(np.tanh((fast - slow) / (swing * 2))))


class MomentumAnalyst(Analyst):
    key = "momentum"
    title = "Momentum"
    persona = "Tempo"
    summary = "RSI and rate of change. Flags exhaustion: overbought markets lean down, oversold lean up."
    defaults = {"period": 14, "lookback": 12}

    def read(self, s: Series) -> Reading:
        r = ta.rsi(s.close, int(self.p["period"]))
        n = int(self.p["lookback"])
        roc = s.close[-1] / s.close[-n - 1] - 1
        if r > 70:
            direction, note = -(r - 70) / 30, f"Overbought, RSI {r:.0f}"
        elif r < 30:
            direction, note = (30 - r) / 30, f"Oversold, RSI {r:.0f}"
        else:
            direction, note = _clip(roc * 40) * 0.5, f"RSI {r:.0f}, {roc:+.2%} over {n} bars"
        return Reading("momentum", _clip(direction), _clip(abs(direction) + 0.2, 0, 1), note)


class VolatilityAnalyst(Analyst):
    key = "volatility"
    title = "Volatility"
    persona = "Seismo"
    summary = "Compares current volatility with its own history. Warns the council when the market turns violent."
    defaults = {"window": 100}

    def read(self, s: Series) -> Reading:
        a = ta.atr(s.high, s.low, s.close) / s.close
        w = int(min(self.p["window"], len(a) - 1))
        ratio = a[-1] / (np.median(a[-w:]) or 1e-9)
        if ratio > 1.6:
            return Reading("risk", -1.0, _clip((ratio - 1.6) / 1.4 + 0.5, 0, 1),
                           f"Volatility {ratio:.1f}x normal, reduce risk")
        if ratio < 0.7:
            return Reading("risk", 1.0, 0.4, f"Unusually quiet, {ratio:.1f}x normal volatility")
        return Reading("risk", 0.0, 0.2, f"Volatility normal ({ratio:.1f}x)")


class BreadthAnalyst(Analyst):
    key = "breadth"
    title = "Breadth"
    persona = "Horizon"
    summary = "Looks at every watched market at once: how many trend up, and whether they all move together."
    defaults = {"ema": 50}
    min_bars = 60

    def read(self, s: Series) -> Reading | None:  # pragma: no cover - cross-market only
        return None

    def read_all(self, series: dict[str, Series]) -> dict[str, Reading]:
        ready = {k: s for k, s in series.items() if len(s) >= self.min_bars}
        if len(ready) < 2:
            return {}
        above = {k: s.close[-1] > ta.ema(s.close, int(self.p["ema"]))[-1] for k, s in ready.items()}
        share = sum(above.values()) / len(above)
        n = min(len(s) for s in ready.values()) - 1
        rets = np.array([ta.log_returns(s.close[-n - 1:])[-50:] for s in ready.values()])
        corr = np.corrcoef(rets) if rets.shape[1] > 2 else np.eye(len(ready))
        avg_corr = float((corr.sum() - len(ready)) / max(len(ready) ** 2 - len(ready), 1))
        tilt = (share - 0.5) * 2
        out = {}
        for k in ready:
            own = 0.3 if above[k] else -0.3
            direction = _clip(tilt * 0.7 + own)
            out[k] = Reading("breadth", direction, _clip(abs(tilt) * (0.5 + avg_corr / 2) + 0.2, 0, 1),
                             f"{share:.0%} of markets above EMA{int(self.p['ema'])}, "
                             f"average correlation {avg_corr:+.2f}")
        return out


class VolumeAnalyst(Analyst):
    key = "volume"
    title = "Volume"
    persona = "Tide"
    summary = ("Checks whether volume confirms the move. Big volume behind a move means conviction; "
               "a move on thin volume is suspect.")
    defaults = {"window": 50, "move": 5}

    def read(self, s: Series) -> Reading | None:
        w, n = int(self.p["window"]), int(self.p["move"])
        base = np.median(s.volume[-w:-n]) if len(s.volume) > w else 0
        if base <= 0:
            return None
        surge = s.volume[-n:].mean() / base
        move = s.close[-1] / s.close[-n - 1] - 1
        sign = 1.0 if move > 0 else -1.0
        if surge > 1.5:
            return Reading("flow", sign * _clip((surge - 1) / 2, 0, 1), _clip(surge / 3, 0.3, 1),
                           f"Volume {surge:.1f}x normal behind a {move:+.2%} move")
        if surge < 0.6 and abs(move) > 0:
            return Reading("flow", -sign * 0.3, 0.35,
                           f"Move of {move:+.2%} on thin volume ({surge:.1f}x), weak conviction")
        return Reading("flow", 0.0, 0.2, f"Volume normal ({surge:.1f}x)")


class RegimeAnalyst(Analyst):
    key = "regime"
    title = "Regime"
    persona = "Compass"
    summary = ("Measures how clean the price path is. Clean paths favour trend followers; "
               "noisy chop favours mean reversion.")
    defaults = {"window": 30}

    def read(self, s: Series) -> Reading:
        er = ta.efficiency_ratio(s.close, int(self.p["window"]))
        drift = s.close[-1] / s.close[-int(self.p["window"]) - 1] - 1
        if er > 0.45:
            return Reading("trend", (1 if drift > 0 else -1) * _clip(er, 0, 1), er,
                           f"Clean {'up' if drift > 0 else 'down'}trend, efficiency {er:.2f}")
        return Reading("trend", 0.0, 0.25, f"Choppy market, efficiency {er:.2f}: trend signals unreliable")


class StructureAnalyst(Analyst):
    key = "structure"
    title = "Structure"
    persona = "Mason"
    summary = ("Maps support and resistance from the recent range. Near support leans up, "
               "pressed against resistance leans down.")
    defaults = {"window": 120}

    def read(self, s: Series) -> Reading | None:
        w = int(min(self.p["window"], len(s) - 1))
        hi, lo = s.high[-w:].max(), s.low[-w:].min()
        if hi <= lo:
            return None
        pos = (s.price - lo) / (hi - lo)
        if pos > 0.97:
            return Reading("structure", 0.4, 0.5, f"Testing range high {price_str(hi)}: breakout or rejection next")
        if pos < 0.03:
            return Reading("structure", -0.4, 0.5, f"Testing range low {price_str(lo)}: breakdown risk")
        direction = (0.5 - pos) * 1.2
        where = "near support" if pos < 0.25 else "near resistance" if pos > 0.75 else "mid-range"
        return Reading("structure", _clip(direction), _clip(abs(direction) + 0.15, 0, 1),
                       f"Price {where} ({pos:.0%} of the {w}-bar range {price_str(lo)} to {price_str(hi)})")


class DivergenceAnalyst(Analyst):
    key = "divergence"
    title = "Divergence"
    persona = "Contra"
    summary = "Spots momentum that disagrees with price: a new high on weaker RSI often comes before a turn."
    defaults = {"window": 30}

    def read(self, s: Series) -> Reading:
        w = int(self.p["window"])
        r = ta.rsi_series(s.close)
        half = w // 2
        p_old, p_new = s.close[-w:-half], s.close[-half:]
        r_old, r_new = r[-w:-half], r[-half:]
        if p_new.max() > p_old.max() and r_new.max() < r_old.max() - 3:
            return Reading("momentum", -0.6, 0.6,
                           f"Bearish divergence: higher high, RSI {r_new.max():.0f} vs {r_old.max():.0f}")
        if p_new.min() < p_old.min() and r_new.min() > r_old.min() + 3:
            return Reading("momentum", 0.6, 0.6,
                           f"Bullish divergence: lower low, RSI {r_new.min():.0f} vs {r_old.min():.0f}")
        return Reading("momentum", 0.0, 0.15, "No divergence")


class RelativeStrengthAnalyst(Analyst):
    key = "relative"
    title = "Relative Strength"
    persona = "Ranker"
    summary = ("Ranks every watched market by recent return. Money tends to keep flowing to leaders "
               "and away from laggards.")
    defaults = {"lookback": 40}

    def read(self, s: Series) -> Reading | None:  # pragma: no cover - cross-market only
        return None

    def read_all(self, series: dict[str, Series]) -> dict[str, Reading]:
        n = int(self.p["lookback"])
        rets = {k: s.close[-1] / s.close[-n - 1] - 1 for k, s in series.items() if len(s) > n + 1}
        if len(rets) < 2:
            return {}
        ranked = sorted(rets, key=rets.get, reverse=True)
        out = {}
        for i, k in enumerate(ranked):
            score = 1 - 2 * i / (len(ranked) - 1)  # +1 leader .. -1 laggard
            out[k] = Reading("breadth", score * 0.7, 0.5,
                             f"Rank {i + 1} of {len(ranked)} over {n} bars ({rets[k]:+.2%})")
        return out


class FundingAnalyst(Analyst):
    key = "funding"
    title = "Funding"
    persona = "Carry"
    summary = ("Reads the perpetual futures funding rate. When longs pay a lot to stay in, the trade is crowded "
               "and tends to unwind; deeply negative funding means the shorts are crowded instead.")
    defaults = {"normal": 0.0001, "crowded": 0.0005}
    markets = ("crypto",)
    needs_extras = True

    def read(self, s: Series) -> Reading | None:
        rate = s.extra.get("funding_rate")
        if rate is None:
            return None
        normal, crowded = self.p["normal"], self.p["crowded"]
        excess = (rate - normal) / (crowded - normal)
        per8h = f"{rate * 100:+.3f}% per 8h"
        if excess > 1:
            return Reading("positioning", -_clip(0.3 + (excess - 1) * 0.2, 0, 0.8), _clip(0.4 + excess * 0.1, 0, 0.8),
                           f"Funding {per8h}: longs are crowded and paying for it")
        if rate < -normal * 2:
            depth = _clip(-rate / crowded, 0, 1)
            return Reading("positioning", 0.3 + depth * 0.4, 0.4 + depth * 0.3,
                           f"Funding {per8h}: shorts are crowded, a squeeze is possible")
        return Reading("positioning", 0.0, 0.15, f"Funding {per8h}, nothing unusual")


class OpenInterestAnalyst(Analyst):
    key = "open_interest"
    title = "Open Interest"
    persona = "Crowd"
    summary = ("Compares the change in futures open interest with the price move. New money behind a move "
               "confirms it; a move while positions close is short covering or liquidation and tends to fade.")
    defaults = {"min_change": 0.01}
    markets = ("crypto",)
    needs_extras = True

    def read(self, s: Series) -> Reading | None:
        oi = s.extra.get("oi_change")
        minutes = s.extra.get("oi_minutes") or 60.0
        if oi is None:
            return None
        bars = int(max(2, min(len(s) - 1, minutes)))  # live bars are one minute
        move = s.close[-1] / s.close[-bars - 1] - 1
        span = f"over {minutes:.0f} min"
        if abs(oi) < self.p["min_change"] or abs(move) < 0.001:
            return Reading("positioning", 0.0, 0.15, f"Open interest {oi:+.1%} {span}, no clear story")
        up = 1.0 if move > 0 else -1.0
        if oi > 0:  # fresh positions in the direction of the move
            return Reading("positioning", up * _clip(0.3 + oi * 10, 0, 0.8), _clip(0.35 + oi * 5, 0, 0.75),
                           f"Price {move:+.2%} with open interest {oi:+.1%} {span}: new money behind the move")
        return Reading("positioning", -up * 0.35, 0.4,
                       f"Price {move:+.2%} while open interest fell {oi:.1%} {span}: positions closing, move may fade")


class OrderBookAnalyst(Analyst):
    key = "order_book"
    title = "Order Book"
    persona = "Depth"
    summary = ("Weighs resting buy orders against sell orders near the price. A heavy bid side leans up, a heavy "
               "ask side leans down. Books can be spoofed, so it speaks softly and the council learns "
               "how far to trust it.")
    defaults = {"threshold": 0.2}
    markets = ("crypto",)
    needs_extras = True

    def read(self, s: Series) -> Reading | None:
        imb = s.extra.get("book_imbalance")
        if imb is None:
            return None
        if abs(imb) < self.p["threshold"]:
            return Reading("flow", 0.0, 0.15, f"Book balanced ({imb:+.0%} bid minus ask)")
        side = "Buyers" if imb > 0 else "Sellers"
        return Reading("flow", _clip(imb * 0.8), _clip(0.25 + abs(imb) * 0.3, 0, 0.55),
                       f"{side} stack the book near the price ({imb:+.0%} bid minus ask)")


class CorrelationAnalyst(Analyst):
    key = "correlation"
    title = "Correlation"
    persona = "Link"
    summary = ("Finds the market each one moves with most closely. When the partner has moved and this one "
               "has not caught up yet, it expects the gap to close.")
    defaults = {"window": 100, "move": 10, "min_corr": 0.6}

    def read(self, s: Series) -> Reading | None:  # pragma: no cover - cross-market only
        return None

    def read_all(self, series: dict[str, Series]) -> dict[str, Reading]:
        w, n = int(self.p["window"]), int(self.p["move"])
        ready = {k: s for k, s in series.items() if len(s) > w + 1}
        if len(ready) < 2:
            return {}
        corr = ta.correlations({k: s.close for k, s in ready.items()}, w)
        out = {}
        for k, s in ready.items():
            partner, rho = max(((o, corr.get((k, o), 0.0)) for o in ready if o != k), key=lambda x: x[1])
            if rho < self.p["min_corr"]:
                out[k] = Reading("breadth", 0.0, 0.15, f"Moves on its own (best match {rho:+.2f})")
                continue
            mine, theirs = ta.log_returns(s.close[-w - 1:]), ta.log_returns(ready[partner].close[-w - 1:])
            beta = float(np.cov(mine, theirs)[0, 1] / (np.var(theirs) or 1e-12))
            gap = float(beta * theirs[-n:].sum() - mine[-n:].sum())
            noise = float(np.std(mine) * np.sqrt(n)) or 1e-9
            lean = _clip(gap / noise / 2) * rho
            name = partner.split(":", 1)[-1]
            if abs(lean) < 0.15:
                note = f"In step with {name} (correlation {rho:.2f})"
            else:
                note = (f"Lagging {name} by {abs(gap):.2%} (correlation {rho:.2f}): "
                        + ("room to catch up" if gap > 0 else "ahead of it, may give back"))
            out[k] = Reading("breadth", lean, _clip(0.2 + abs(lean) * 0.5, 0, 0.7), note)
        return out


ANALYSTS = {cls.key: cls for cls in (
    TrendAnalyst, MomentumAnalyst, VolatilityAnalyst, BreadthAnalyst, VolumeAnalyst,
    RegimeAnalyst, StructureAnalyst, DivergenceAnalyst, RelativeStrengthAnalyst,
    CorrelationAnalyst, FundingAnalyst, OpenInterestAnalyst, OrderBookAnalyst,
)}
