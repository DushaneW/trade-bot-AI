"""Risk manager: the only path from a bot's intent to an order.

Every order is checked against the bot's own limits and the global kill
switch before it reaches a broker. Bots cannot bypass this.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .ledger import Ledger
from .models import Market, Side

CORRELATED = 0.5  # return correlation from which two markets count as one bet for position limits


@dataclass
class RiskLimits:
    max_position_pct: float = 0.5   # share of bot equity in a single instrument
    stop_loss_pct: float = 0.04     # per-position loss that forces an exit
    trailing_stop_pct: float = 0.0  # 0 disables trailing
    max_drawdown_pct: float = 0.3   # bot pauses when equity falls this far below budget
    daily_loss_pct: float = 0.1     # bot pauses for the day after losing this much
    min_order_value: float = 5.0    # exchanges reject dust orders
    max_correlated_pct: float = 0.8  # this market plus look-alike markets, as a share of equity

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> RiskLimits:
        known = cls.__dataclass_fields__
        return cls(**{k: float(v) for k, v in (data or {}).items() if k in known})


PROFILES: dict[str, dict] = {
    "careful": {"label": "Careful", "size": 0.7,
                "hint": "Small positions, 3% stop loss, pauses after a 20% drawdown.",
                "limits": RiskLimits(max_position_pct=0.35, stop_loss_pct=0.03, max_drawdown_pct=0.2,
                                     daily_loss_pct=0.06, max_correlated_pct=0.5)},
    "balanced": {"label": "Balanced", "size": 1.0,
                 "hint": "The defaults: 4% stop loss, half the budget per market at most.",
                 "limits": RiskLimits()},
    "bold": {"label": "Bold", "size": 1.3,
             "hint": "Bigger positions, 6% stop loss, pauses after a 40% drawdown.",
             "limits": RiskLimits(max_position_pct=0.8, stop_loss_pct=0.06, max_drawdown_pct=0.4,
                                  daily_loss_pct=0.15, max_correlated_pct=1.4)},
}


def conviction(direction: float) -> float:
    """Scale an entry by how strongly the council agrees: 0.75x when split, up to 1.25x when unanimous."""
    return 0.75 + 0.5 * min(abs(direction), 1.0)


@dataclass
class Verdict:
    qty: float
    reason: str | None = None  # set when the order was reduced or rejected

    @property
    def approved(self) -> bool:
        return self.qty > 0


class RiskManager:
    def __init__(self) -> None:
        self.halted = False  # global kill switch

    def check_bot(self, ledger: Ledger, limits: RiskLimits, equity: float,
                  day_start: float) -> tuple[str, str] | None:
        """Return (kind, reason) when the bot must pause, else None.

        kind is "drawdown" (stays paused until the user resumes it) or
        "daily" (resumes by itself on the next UTC day).
        """
        if equity <= ledger.budget * (1 - limits.max_drawdown_pct):
            return "drawdown", f"Drawdown limit hit: equity {equity:.2f} of {ledger.budget:.2f} budget"
        if day_start > 0 and equity <= day_start * (1 - limits.daily_loss_pct):
            return "daily", f"Daily loss limit hit: {equity - day_start:.2f} today"
        return None

    def stop_triggered(self, ledger: Ledger, limits: RiskLimits, key: str, price: float) -> str | None:
        pos = ledger.position(key)
        if not pos.is_open or pos.avg_price <= 0:
            return None
        long = pos.qty > 0
        move = (price - pos.avg_price) / pos.avg_price * (1 if long else -1)
        if move <= -limits.stop_loss_pct:
            return f"Stop loss at {move:.1%}"
        if limits.trailing_stop_pct > 0:
            pos.peak_price = max(pos.peak_price, price) if long else min(pos.peak_price or price, price)
            retrace = (pos.peak_price - price) / pos.peak_price * (1 if long else -1)
            if move > 0 and retrace >= limits.trailing_stop_pct:
                return f"Trailing stop, {retrace:.1%} off the peak"
        return None

    def review(self, ledger: Ledger, limits: RiskLimits, market: Market,
               key: str, side: Side, qty: float, price: float, equity: float,
               correlated: float = 0.0) -> Verdict:
        """`correlated`: money already in other markets that move with this one, weighted by correlation."""
        if self.halted:
            return Verdict(0, "Kill switch is on")
        if qty <= 0 or price <= 0:
            return Verdict(0, "Invalid order size or price")

        pos = ledger.position(key)
        reducing = (pos.qty > 0 and side == Side.SELL) or (pos.qty < 0 and side == Side.BUY)
        if reducing:
            # Exits are always allowed, capped at the open size.
            return Verdict(min(qty, abs(pos.qty)))

        if side == Side.SELL and market != Market.FOREX:
            return Verdict(0, "Short selling is only enabled for forex")

        reason = None
        room = equity * limits.max_position_pct - ledger.exposure(key, price)
        if qty * price > room:
            qty = max(room, 0) / price
            reason = "Reduced to the position size limit"
        if correlated > 0:
            # BTC, ETH and SOL often move as one: three positions there are one big bet.
            shared = equity * limits.max_correlated_pct - ledger.exposure(key, price) - correlated
            if qty * price > shared:
                qty = max(shared, 0) / price
                room = min(room, max(shared, 0))
                reason = "Reduced: the trader already holds markets that move with this one"
        if side == Side.BUY and qty * price * 1.002 > ledger.cash:
            qty = max(ledger.cash / 1.002, 0) / price
            reason = "Reduced to available budget"
        if qty * price < limits.min_order_value:
            # Small budgets: a smaller-than-minimum idea becomes a minimum-size order, as long as the trader
            # can afford it and it still fits the position limit. Otherwise skip it.
            minimum = limits.min_order_value * 1.02
            affordable = side == Side.SELL or minimum * 1.002 <= ledger.cash
            if qty > 0 and affordable and minimum <= max(room, 0):
                return Verdict(minimum / price, "Raised to the exchange minimum")
            return Verdict(0, reason or "Below the exchange minimum and the budget can't cover it")
        return Verdict(qty, reason)
