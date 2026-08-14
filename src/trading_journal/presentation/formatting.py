"""Display-only formatting for trading metrics."""

from __future__ import annotations

from decimal import Decimal


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


def format_percent(value: str | Decimal, *, signed: bool = False, decimal_places: int = 1) -> str:
    if signed:
        return format_signed(value, "%", decimal_places)
    return f"{format_number(value, decimal_places)}%"


def format_score(value: str | Decimal) -> str:
    return f"{format_number(value, 0)}%"


def format_count(value: int) -> str:
    return f"{value:,}"
