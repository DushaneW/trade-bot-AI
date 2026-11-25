"""Traders: each has its own idea of an edge and listens to the council.

A trader never sizes in money. It returns the fraction of its equity it
wants to commit; the risk manager turns that into an order or refuses.
"""
from __future__ import annotations

from .. import indicators as ta
from ..models import Side
from .analysts import price_str
from .base import Consensus, Intent, PositionView, Series, Trader


def _risk_scale(council: Consensus) -> float:
    """Shrink entries when analysts warn about turbulence."""
    return max(0.25, 1.0 - council.risk_off * 0.75)


class TrendRider(Trader):
    key = "trend_rider"
    title = "Trend Rider"
    persona = "Rider"
    summary = "Buys EMA crossovers when the council agrees on direction; exits on the reverse cross."
    defaults = {"fast": 12, "slow": 34, "agree": 0.1, "size": 0.45}

    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None:
        fast = ta.ema(s.close, int(self.p["fast"]))
        slow = ta.ema(s.close, int(self.p["slow"]))
        up = fast[-1] > slow[-1]
        if pos.flat and up and fast[-2] <= slow[-2] and council.direction >= self.p["agree"]:
            return Intent(Side.BUY, self.p["size"] * _risk_scale(council),
                          f"EMA{int(self.p['fast'])} crossed above EMA{int(self.p['slow'])}, "
                          f"council {council.direction:+.2f}")
        if pos.long and not up:
            return Intent(Side.SELL, 0, "Trend turned down", exit=True)
        return None


class MeanReverter(Trader):
    key = "mean_reverter"
    title = "Mean Reverter"
    persona = "Elastic"
    summary = "Buys sharp dips below the 20-bar mean and sells the bounce. Stands aside in turbulence."
    defaults = {"window": 20, "entry_z": 2.0, "exit_z": 0.0, "size": 0.35}

    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None:
        z = ta.zscore(s.close, int(self.p["window"]))
        if pos.flat and z <= -self.p["entry_z"] and council.risk_off < 0.6:
            return Intent(Side.BUY, self.p["size"] * _risk_scale(council),
                          f"Price {z:.1f} standard deviations below the mean")
        if pos.long and z >= self.p["exit_z"]:
            return Intent(Side.SELL, 0, f"Back to the mean (z {z:+.1f})", exit=True)
        return None


class BreakoutHunter(Trader):
    key = "breakout"
    title = "Breakout Hunter"
    persona = "Breaker"
    summary = "Buys new 40-bar highs, exits on a 15-bar low. Ignores the council except for risk warnings."
    defaults = {"entry": 40, "exit": 15, "size": 0.4}

    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None:
        n_in, n_out = int(self.p["entry"]), int(self.p["exit"])
        prior_high = s.high[-n_in - 1:-1].max()
        prior_low = s.low[-n_out - 1:-1].min()
        if pos.flat and s.price > prior_high:
            return Intent(Side.BUY, self.p["size"] * _risk_scale(council),
                          f"Broke the {n_in}-bar high at {price_str(prior_high)}")
        if pos.long and s.price < prior_low:
            return Intent(Side.SELL, 0, f"Lost the {n_out}-bar low", exit=True)
        return None


class Accumulator(Trader):
    key = "accumulator"
    title = "Accumulator"
    persona = "Stacker"
    summary = "Buys small slices on a schedule, larger ones when oversold. Takes profit at a fixed gain."
    defaults = {"every": 30, "slice": 0.08, "take_profit": 0.06}

    def __init__(self, params: dict[str, float] | None = None) -> None:
        super().__init__(params)
        self._bars = 0

    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None:
        self._bars += 1
        if pos.long and s.price >= pos.avg_price * (1 + self.p["take_profit"]):
            return Intent(Side.SELL, 0, f"Take profit, +{s.price / pos.avg_price - 1:.1%}", exit=True)
        if self._bars % int(self.p["every"]) == 0:
            r = ta.rsi(s.close)
            boost = 2.0 if r < 35 else 1.0
            return Intent(Side.BUY, self.p["slice"] * boost * _risk_scale(council),
                          f"Scheduled buy, RSI {r:.0f}" + (" (oversold, double slice)" if boost > 1 else ""))
        return None


class CouncilFollower(Trader):
    key = "council"
    title = "Council Follower"
    persona = "Delegate"
    summary = "Has no indicators of its own. Trades only when the weighted vote of all analysts is strong."
    defaults = {"enter": 0.35, "leave": 0.0, "size": 0.4}

    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None:
        if pos.flat and council.direction >= self.p["enter"] and council.risk_off < 0.5:
            return Intent(Side.BUY, self.p["size"] * _risk_scale(council),
                          f"Council vote {council.direction:+.2f} from {len(council.voices)} analysts")
        if pos.long and council.direction <= self.p["leave"]:
            return Intent(Side.SELL, 0, f"Council support faded to {council.direction:+.2f}", exit=True)
        return None


TRADERS = {cls.key: cls for cls in (TrendRider, MeanReverter, BreakoutHunter, Accumulator, CouncilFollower)}
