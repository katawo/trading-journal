"""Consistent visual vocabulary for imported logical-trade facts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TradeTag:
    label: str
    color: str
    icon: str


def direction_tag(direction: str) -> TradeTag:
    if direction.casefold() == "short":
        return TradeTag("Short", "orange", ":material/trending_down:")
    return TradeTag("Long", "blue", ":material/trending_up:")


def outcome_tag(outcome: str | Decimal) -> TradeTag:
    normalized = str(outcome).casefold()
    if normalized in {"profit", "win"}:
        return TradeTag("Profit", "green", ":material/arrow_upward:")
    if normalized == "loss":
        return TradeTag("Loss", "red", ":material/arrow_downward:")
    if normalized == "breakeven":
        return TradeTag("Breakeven", "gray", ":material/horizontal_rule:")
    value = Decimal(outcome)
    if value > 0:
        return TradeTag("Profit", "green", ":material/arrow_upward:")
    if value < 0:
        return TradeTag("Loss", "red", ":material/arrow_downward:")
    return TradeTag("Breakeven", "gray", ":material/horizontal_rule:")
