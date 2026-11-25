"""Risk manager: the only path from a bot's intent to an order.

Every order is checked against the bot's own limits and the global kill
switch before it reaches a broker. Bots cannot bypass this.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .ledger import Ledger
from .models import Market, Side


@dataclass
class RiskLimits:
    max_position_pct: float = 0.5   # share of bot equity in a single instrument
    stop_loss_pct: float = 0.04     # per-position loss that forces an exit
    trailing_stop_pct: float = 0.0  # 0 disables trailing
    max_drawdown_pct: float = 0.3   # bot pauses when equity falls this far below budget
    daily_loss_pct: float = 0.1     # bot pauses for the day after losing this much
    min_order_value: float = 5.0    # exchanges reject dust orders

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "RiskLimits":
        known = cls.__dataclass_fields__
        return cls(**{k: float(v) for k, v in (data or {}).items() if k in known})


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
               key: str, side: Side, qty: float, price: float, equity: float) -> Verdict:
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
        if side == Side.BUY and qty * price * 1.002 > ledger.cash:
            qty = max(ledger.cash / 1.002, 0) / price
            reason = "Reduced to available budget"
        if qty * price < limits.min_order_value:
            return Verdict(0, reason or "Below minimum order value")
        return Verdict(qty, reason)
