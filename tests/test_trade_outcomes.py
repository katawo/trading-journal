from decimal import Decimal

import pytest

from trading_journal.domain.trade_outcomes import classify_trade_outcome


@pytest.mark.parametrize("result_r", ("-0.05", "0", "0.05"))
def test_five_percent_delta_includes_both_boundaries(result_r: str) -> None:
    assert classify_trade_outcome("1", result_r, 5) == "breakeven"


@pytest.mark.parametrize(
    ("net_pnl", "result_r", "expected"),
    (("1", "0.0501", "profit"), ("-1", "-0.0501", "loss")),
)
def test_results_outside_the_delta_keep_their_direction(
    net_pnl: str, result_r: str, expected: str
) -> None:
    assert classify_trade_outcome(net_pnl, result_r, 5) == expected


def test_zero_percent_restores_exact_zero_classification() -> None:
    assert classify_trade_outcome("0.01", "0.0001", 0) == "profit"
    assert classify_trade_outcome("-0.01", "-0.0001", 0) == "loss"
    assert classify_trade_outcome("0", "0", 0) == "breakeven"


@pytest.mark.parametrize(
    ("net_pnl", "expected"),
    (("1", "profit"), ("-1", "loss"), ("0", "breakeven")),
)
def test_missing_r_falls_back_to_net_pnl_sign(net_pnl: str, expected: str) -> None:
    assert classify_trade_outcome(Decimal(net_pnl), None, 5) == expected


@pytest.mark.parametrize("delta", (-1, 101))
def test_invalid_delta_is_rejected(delta: int) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        classify_trade_outcome("0", "0", delta)
