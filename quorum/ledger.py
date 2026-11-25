"""Per-bot capital ledger.

A bot only ever sees the budget the user gave it. The ledger tracks its
cash, open positions and realised PnL independently of what else sits
on the exchange account, which is what lets several bots share one
account safely.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Fill, Side


@dataclass
class Position:
    qty: float = 0.0          # signed: >0 long, <0 short
    avg_price: float = 0.0
    peak_price: float = 0.0   # best price since entry, for trailing stops

    @property
    def is_open(self) -> bool:
        return abs(self.qty) > 1e-12


@dataclass
class Ledger:
    budget: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    realized: float = 0.0
    fees: float = 0.0
    trades: int = 0
    wins: int = 0

    def __post_init__(self) -> None:
        self.cash = self.budget

    def position(self, key: str) -> Position:
        return self.positions.setdefault(key, Position())

    def equity(self, prices: dict[str, float]) -> float:
        value = self.cash
        for key, pos in self.positions.items():
            if pos.is_open:
                value += pos.qty * prices.get(key, pos.avg_price)
        return value

    def exposure(self, key: str, price: float) -> float:
        return abs(self.position(key).qty * price)

    def apply(self, fill: Fill) -> float:
        """Book a fill and return the realised PnL it produced."""
        pos = self.position(fill.instrument_key)
        signed = fill.qty if fill.side == Side.BUY else -fill.qty
        self.cash -= signed * fill.price + fill.fee
        self.fees += fill.fee
        realized = 0.0

        same_direction = pos.qty == 0 or (pos.qty > 0) == (signed > 0)
        if same_direction:
            new_qty = pos.qty + signed
            pos.avg_price = (pos.avg_price * abs(pos.qty) + fill.price * abs(signed)) / abs(new_qty)
            pos.qty = new_qty
            pos.peak_price = fill.price if pos.peak_price == 0 else pos.peak_price
        else:
            closing = min(abs(signed), abs(pos.qty))
            direction = 1 if pos.qty > 0 else -1
            realized = closing * (fill.price - pos.avg_price) * direction - fill.fee
            pos.qty += signed
            if abs(pos.qty) < 1e-12:
                pos.qty, pos.avg_price, pos.peak_price = 0.0, 0.0, 0.0
            elif (pos.qty > 0) != (direction > 0):  # flipped through zero
                pos.avg_price = fill.price
                pos.peak_price = fill.price
            self.trades += 1
            self.wins += realized > 0
            self.realized += realized
        return realized

    def to_state(self) -> dict:
        return {
            "budget": self.budget, "cash": self.cash, "realized": self.realized, "fees": self.fees,
            "trades": self.trades, "wins": self.wins,
            "positions": {k: [p.qty, p.avg_price, p.peak_price] for k, p in self.positions.items() if p.is_open},
        }

    @classmethod
    def from_state(cls, state: dict) -> "Ledger":
        led = cls(float(state["budget"]))
        led.cash = float(state["cash"])
        led.realized = float(state.get("realized", 0))
        led.fees = float(state.get("fees", 0))
        led.trades = int(state.get("trades", 0))
        led.wins = int(state.get("wins", 0))
        led.positions = {k: Position(*v) for k, v in state.get("positions", {}).items()}
        return led

    def snapshot(self, prices: dict[str, float]) -> dict:
        return {
            "budget": self.budget,
            "cash": self.cash,
            "equity": self.equity(prices),
            "realized": self.realized,
            "fees": self.fees,
            "trades": self.trades,
            "win_rate": self.wins / self.trades if self.trades else None,
            "positions": {
                k: {"qty": p.qty, "avg_price": p.avg_price, "price": prices.get(k, p.avg_price)}
                for k, p in self.positions.items() if p.is_open
            },
        }
