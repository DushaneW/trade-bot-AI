"""How the council learns.

Every directional reading is a prediction. After `horizon_bars` the
outcome is scored against what the price actually did, and each
analyst's accuracy feeds its voting weight. Analysts that keep being
wrong fade out of the consensus; analysts that keep being right get
louder. Volatility warnings are scored on whether the move that followed
was unusually large.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .bots import Consensus, Reading

NEUTRAL_BAND = 0.15
MIN_WEIGHT, MAX_WEIGHT = 0.2, 2.0
PRIOR = 5  # pseudo-observations at 50% so a few lucky calls do not dominate


@dataclass
class Prediction:
    analyst_id: str
    key: str
    kind: str
    direction: float
    price: float
    typical_move: float  # expected absolute move over the horizon, as a fraction
    due_ts: int


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
        return max(MIN_WEIGHT, min(MAX_WEIGHT, 1 + (self.accuracy - 0.5) * 6))

    def record(self, hit: bool) -> None:
        hit = bool(hit)  # numpy booleans would leak numpy ints into JSON
        self.calls += 1
        self.hits += hit
        self.history = (self.history + [int(hit)])[-40:]

    def to_dict(self) -> dict:
        return {"hits": self.hits, "calls": self.calls, "history": self.history,
                "accuracy": self.accuracy, "weight": self.weight}

    @classmethod
    def from_dict(cls, d: dict) -> "Reputation":
        return cls(d.get("hits", 0), d.get("calls", 0), list(d.get("history", [])))


class Council:
    def __init__(self, horizon_bars: int = 8, freshness_bars: int = 3) -> None:
        self.horizon_bars = horizon_bars
        self.freshness_bars = freshness_bars
        self.reputation: dict[str, Reputation] = {}
        self.latest: dict[tuple[str, str], tuple[int, Reading]] = {}  # (analyst, key) -> (ts, reading)
        self.pending: list[Prediction] = []

    def rep(self, analyst_id: str) -> Reputation:
        return self.reputation.setdefault(analyst_id, Reputation())

    def hear(self, analyst_id: str, key: str, reading: Reading, price: float,
             ts: int, bar_ms: int, atr_frac: float) -> None:
        self.latest[(analyst_id, key)] = (ts, reading)
        if abs(reading.direction) < NEUTRAL_BAND:
            return
        typical = atr_frac * math.sqrt(self.horizon_bars)
        self.pending.append(Prediction(analyst_id, key, reading.kind, reading.direction, price,
                                       typical, ts + bar_ms * self.horizon_bars))
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
            done.append((p.analyst_id, hit))
        self.pending = keep
        return done

    def consensus(self, key: str, now: int, bar_ms: int) -> Consensus:
        horizon = bar_ms * self.freshness_bars
        num = den = risk_num = risk_den = 0.0
        voices = []
        for (analyst_id, k), (ts, r) in self.latest.items():
            if k != key or now - ts > horizon:
                continue
            w = self.rep(analyst_id).weight
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
