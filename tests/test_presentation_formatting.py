from decimal import Decimal

from trading_journal.presentation.formatting import (
    currency_decimal_places,
    currency_prefix,
    format_count,
    format_currency,
    format_percent,
    format_r,
    format_score,
)


def test_formats_signed_currency_and_unsigned_balances() -> None:
    assert format_currency("1234.5", "USD") == "+$1,234.50"
    assert format_currency("-1234.5", "USD") == "−$1,234.50"
    assert format_currency("1234.5", "USD", signed=False) == "$1,234.50"
    assert format_currency(Decimal("0"), "USD") == "$0.00"


def test_formats_jpy_without_fractional_digits() -> None:
    assert currency_decimal_places("JPY") == 0
    assert currency_prefix("JPY") == "¥"
    assert format_currency("1234.5", "JPY") == "+¥1,234"


def test_formats_trading_metrics_consistently() -> None:
    assert format_r("1.25") == "+1.25R"
    assert format_r("-1.25") == "−1.25R"
    assert format_percent("12.54") == "12.5%"
    assert format_percent("12.54", signed=True) == "+12.5%"
    assert format_score("84.6") == "85%"
    assert format_count(12345) == "12,345"


def test_uses_currency_code_for_unknown_currency() -> None:
    assert format_currency("42", "sgd") == "+SGD 42.00"
