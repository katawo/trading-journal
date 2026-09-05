"""Outcome classification policy for completed logical trades."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal


TradeOutcome = Literal["profit", "loss", "breakeven"]


def classify_trade_outcome(
    net_pnl: str | Decimal,
    result_r: str | Decimal | None,
    breakeven_threshold_percent: int,
) -> TradeOutcome:
    """Classify a trade using an inclusive R-based band around zero.

    When standard-1R evidence is unavailable, retain the journal's historical
    sign-based behavior so every completed trade still has an outcome.
    """

    if not 0 <= breakeven_threshold_percent <= 100:
        raise ValueError("Breakeven threshold must be between 0 and 100 percent")
    pnl = Decimal(net_pnl)
    if result_r is not None:
        threshold_r = Decimal(breakeven_threshold_percent) / Decimal("100")
        if abs(Decimal(result_r)) <= threshold_r:
            return "breakeven"
    elif pnl == 0:
        return "breakeven"
    return "profit" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
