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
    summary = "Checks whether volume confirms the move. Big volume behind a move means conviction; a move on thin volume is suspect."
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
            return Reading("flow", -sign * 0.3, 0.35, f"Move of {move:+.2%} on thin volume ({surge:.1f}x), weak conviction")
        return Reading("flow", 0.0, 0.2, f"Volume normal ({surge:.1f}x)")


class RegimeAnalyst(Analyst):
    key = "regime"
    title = "Regime"
    persona = "Compass"
    summary = "Measures how clean the price path is. Clean paths favour trend followers; noisy chop favours mean reversion."
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
    summary = "Maps support and resistance from the recent range. Near support leans up, pressed against resistance leans down."
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
            return Reading("momentum", -0.6, 0.6, f"Bearish divergence: higher high, RSI {r_new.max():.0f} vs {r_old.max():.0f}")
        if p_new.min() < p_old.min() and r_new.min() > r_old.min() + 3:
            return Reading("momentum", 0.6, 0.6, f"Bullish divergence: lower low, RSI {r_new.min():.0f} vs {r_old.min():.0f}")
        return Reading("momentum", 0.0, 0.15, "No divergence")


class RelativeStrengthAnalyst(Analyst):
    key = "relative"
    title = "Relative Strength"
    persona = "Ranker"
    summary = "Ranks every watched market by recent return. Money tends to keep flowing to leaders and away from laggards."
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


ANALYSTS = {cls.key: cls for cls in (
    TrendAnalyst, MomentumAnalyst, VolatilityAnalyst, BreadthAnalyst, VolumeAnalyst,
    RegimeAnalyst, StructureAnalyst, DivergenceAnalyst, RelativeStrengthAnalyst,
)}
