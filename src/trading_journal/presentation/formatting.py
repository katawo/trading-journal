"""Display-only formatting for trading metrics."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import streamlit as st


AccentMetricTone = Literal["positive", "negative", "warning", "info", "neutral"]


_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "CHF": "CHF",
    "NZD": "NZ$",
}
_ZERO_DECIMAL_CURRENCIES = frozenset({"JPY"})


def currency_decimal_places(currency: str) -> int:
    """Return the display precision for a supported account currency."""
    return 0 if currency.upper() in _ZERO_DECIMAL_CURRENCIES else 2


def currency_prefix(currency: str) -> str:
    """Return a compact currency marker for UI labels and chart axes."""
    code = currency.upper()
    return _CURRENCY_SYMBOLS.get(code, f"{code} ")


def format_number(value: str | Decimal, decimal_places: int = 2) -> str:
    return f"{Decimal(value):,.{decimal_places}f}"


def format_signed(value: str | Decimal, suffix: str = "", decimal_places: int = 2) -> str:
    number = Decimal(value)
    prefix = "+" if number > 0 else "−" if number < 0 else ""
    return f"{prefix}{format_number(abs(number), decimal_places)}{suffix}"


def format_currency(value: str | Decimal, currency: str, *, signed: bool = True) -> str:
    """Format a monetary value using the account's display currency."""
    number = Decimal(value)
    prefix = "−" if number < 0 else "+" if signed and number > 0 else ""
    amount = format_number(abs(number), currency_decimal_places(currency))
    return f"{prefix}{currency_prefix(currency)}{amount}"


def format_r(value: str | Decimal) -> str:
    return format_signed(value, "R")


def format_exposure_r(value: str | Decimal) -> str:
    """Format a risk limit or exposure as an unsigned R magnitude."""

    return f"{format_number(abs(Decimal(value)))}R"


def format_percent(value: str | Decimal, *, signed: bool = False, decimal_places: int = 1) -> str:
    if signed:
        return format_signed(value, "%", decimal_places)
    return f"{format_number(value, decimal_places)}%"


def format_score(value: str | Decimal) -> str:
    return f"{format_number(value, 0)}%"


def format_count(value: int) -> str:
    return f"{value:,}"


def render_accent_metric(
    label: str,
    value: str | None,
    *,
    key: str,
    tone: AccentMetricTone,
    delta: str | None = None,
    delta_color: str = "normal",
    delta_arrow: str = "auto",
    delta_description: str | None = None,
) -> None:
    """Render a native st.metric with the shared colored left-border accent.

    The accent styling is injected once, globally, by app.py's
    apply_application_style(); this only needs to match its "dashboard-metric-
    {tone}-" container key convention so every page's status metrics look
    consistent rather than each page inventing its own coloring.
    """
    with st.container(key=f"dashboard-metric-{tone}-{key}"):
        st.metric(
            label,
            value,
            delta,
            delta_color=delta_color,
            delta_arrow=delta_arrow,
            delta_description=delta_description,
            border=True,
        )
