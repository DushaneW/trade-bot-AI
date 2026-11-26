"""How the council learns whom to trust.

Every directional reading is a prediction. After `horizon_bars` it is scored
against what the price actually did. Each analyst keeps three records:

  * overall,
  * per market (an analyst who reads BTC well may read SOL badly),
  * per regime ("trend" or "chop": trend tools shine in trends and mislead in noise).

The vote weight in a given market and regime blends the two specific records,
each shrunk toward the analyst's overall record until it has enough history of
its own. A handful of lucky calls in one corner cannot dominate the vote.
Volatility warnings are scored on whether the move that followed was unusually large.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .bots import Consensus, Reading

NEUTRAL_BAND = 0.15
MIN_WEIGHT, MAX_WEIGHT = 0.2, 2.0
PRIOR = 5     # pseudo-calls at 50% for the overall record
SHRINK = 15   # pseudo-calls at the overall accuracy for each specific record
REGIMES = ("trend", "chop")


@dataclass
class Prediction:
    analyst_id: str
    key: str
    kind: str
    direction: float
    price: float
    typical_move: float  # expected absolute move over the horizon, as a fraction
    due_ts: int
    regime: str | None = None


@dataclass
class Reputation:
    hits: int = 0
    calls: int = 0
    history: list[int] = field(default_factory=list)  # last results, 1 = right

    @property
    def accuracy(self) -> float:
        return (self.hits + PRIOR) / (self.calls + 2 * PRIOR)

    @property
    def weight(self) -> float:
        return weight_of(self.accuracy)

    def shrunk(self, toward: float) -> float:
        """Accuracy pulled toward `toward` until this record has history of its own."""
        return (self.hits + SHRINK * toward) / (self.calls + SHRINK)

    def record(self, hit: bool) -> None:
        hit = bool(hit)  # numpy booleans would leak numpy ints into JSON
        self.calls += 1
        self.hits += hit
        self.history = (self.history + [int(hit)])[-40:]

    def to_dict(self) -> dict:
        return {"hits": self.hits, "calls": self.calls, "history": self.history,
                "accuracy": self.accuracy, "weight": self.weight}

    @classmethod
    def from_dict(cls, d: dict) -> Reputation:
        return cls(d.get("hits", 0), d.get("calls", 0), list(d.get("history", [])))


def weight_of(accuracy: float) -> float:
    return max(MIN_WEIGHT, min(MAX_WEIGHT, 1 + (accuracy - 0.5) * 6))


def regime_of(efficiency: float) -> str:
    return "trend" if efficiency > 0.35 else "chop"


class Council:
    def __init__(self, horizon_bars: int = 8, freshness_bars: int = 3) -> None:
        self.horizon_bars = horizon_bars
        self.freshness_bars = freshness_bars
        # "aid" for the overall record, "aid|m:<key>" per market, "aid|r:<regime>" per regime
        self.reputation: dict[str, Reputation] = {}
        self.latest: dict[tuple[str, str], tuple[int, Reading]] = {}  # (analyst, key) -> (ts, reading)
        self.pending: list[Prediction] = []

    def rep(self, analyst_id: str, context: str | None = None) -> Reputation:
        name = analyst_id if context is None else f"{analyst_id}|{context}"
        return self.reputation.setdefault(name, Reputation())

    def trust(self, analyst_id: str, key: str | None = None, regime: str | None = None) -> float:
        """Vote weight for this analyst in this market and regime."""
        overall = self.rep(analyst_id).accuracy
        specific = []
        if key is not None:
            specific.append(self.rep(analyst_id, f"m:{key}").shrunk(overall))
        if regime is not None:
            specific.append(self.rep(analyst_id, f"r:{regime}").shrunk(overall))
        return weight_of(sum(specific) / len(specific) if specific else overall)

    def profile(self, analyst_id: str) -> dict:
        """Where this analyst is reliable and where it is not, for the dashboard and the Chair."""
        overall = self.rep(analyst_id).accuracy
        markets, regimes = [], []
        for name, rep in self.reputation.items():
            if not name.startswith(f"{analyst_id}|") or rep.calls == 0:
                continue
            ctx = name.split("|", 1)[1]
            row = {"name": ctx[2:], "accuracy": rep.shrunk(overall), "calls": rep.calls}
            (markets if ctx.startswith("m:") else regimes).append(row)
        markets.sort(key=lambda r: -r["calls"])
        return {"markets": markets, "regimes": sorted(regimes, key=lambda r: r["name"] != "trend")}

    def hear(self, analyst_id: str, key: str, reading: Reading, price: float,
             ts: int, bar_ms: int, atr_frac: float, regime: str | None = None) -> None:
        self.latest[(analyst_id, key)] = (ts, reading)
        if abs(reading.direction) < NEUTRAL_BAND:
            return
        typical = atr_frac * math.sqrt(self.horizon_bars)
        self.pending.append(Prediction(analyst_id, key, reading.kind, reading.direction, price,
                                       typical, ts + bar_ms * self.horizon_bars, regime))
        if len(self.pending) > 5000:
            del self.pending[:1000]

    def score(self, prices: dict[str, float], now: int) -> list[tuple[str, bool]]:
        """Resolve predictions that are due. Returns (analyst_id, hit) pairs."""
        done, keep = [], []
        for p in self.pending:
            if p.due_ts > now or p.key not in prices:
                keep.append(p)
                continue
            ret = prices[p.key] / p.price - 1
            if p.kind == "risk":
                big = abs(ret) > p.typical_move
                hit = big if p.direction < 0 else not big
            else:
                hit = ret * p.direction > 0
            self.rep(p.analyst_id).record(hit)
            self.rep(p.analyst_id, f"m:{p.key}").record(hit)
            if p.regime:
                self.rep(p.analyst_id, f"r:{p.regime}").record(hit)
            done.append((p.analyst_id, bool(hit)))
        self.pending = keep
        return done

    def consensus(self, key: str, now: int, bar_ms: int, regime: str | None = None) -> Consensus:
        horizon = bar_ms * self.freshness_bars
        num = den = risk_num = risk_den = 0.0
        voices = []
        for (analyst_id, k), (ts, r) in self.latest.items():
            if k != key or now - ts > horizon:
                continue
            w = self.trust(analyst_id, key, regime)
            voices.append({"analyst": analyst_id, "kind": r.kind, "direction": r.direction,
                           "confidence": r.confidence, "weight": w, "note": r.note})
            if r.kind == "risk":
                risk_num += w * r.confidence * max(-r.direction, 0)
                risk_den += w
            else:
                num += w * r.confidence * r.direction
                den += w * r.confidence
        return Consensus(
            direction=num / den if den else 0.0,
            risk_off=min(risk_num / risk_den, 1.0) if risk_den else 0.0,
            voices=voices,
        )
