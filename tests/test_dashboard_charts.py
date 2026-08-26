from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from trading_journal.application.dashboard import PerformanceBreakdown
from trading_journal.presentation import i18n


_APP_PATH = Path(__file__).parents[1] / "app.py"
_APP_SPEC = spec_from_file_location("trade_compass_dashboard_charts", _APP_PATH)
assert _APP_SPEC is not None and _APP_SPEC.loader is not None
journal_app = module_from_spec(_APP_SPEC)
_APP_SPEC.loader.exec_module(journal_app)


def breakdown(label: str, net_pnl: str, *, trade_count: int = 4, win_rate: str = "50") -> PerformanceBreakdown:
    return PerformanceBreakdown(
        label=label,
        trade_count=trade_count,
        win_count=2,
        loss_count=2,
        breakeven_count=0,
        win_rate=win_rate,
        net_pnl=net_pnl,
        total_r=None,
        r_trade_count=0,
        expectancy_r=None,
        profit_factor="1",
    )


def test_outcome_mix_chart_preserves_counts_percentages_and_semantic_colours() -> None:
    figure = journal_app._build_outcome_mix_figure(win_count=3, loss_count=2, breakeven_count=1)

    assert [trace.name for trace in figure.data] == ["Wins", "Losses", "Breakevens"]
    assert [trace.x[0] for trace in figure.data] == [50.0, 100 / 3, 100 / 6]
    assert [trace.customdata[0][0] for trace in figure.data] == ["3", "2", "1"]
    assert [trace.customdata[0][1] for trace in figure.data] == ["50.0%", "33.3%", "16.7%"]
    assert [trace.marker.color for trace in figure.data] == [
        journal_app._CHART_POSITIVE,
        journal_app._CHART_NEGATIVE,
        journal_app._CHART_NEUTRAL,
    ]
    assert figure.layout.title.text == "Outcome mix"


def test_daily_range_chart_uses_a_zero_baseline_and_colours_each_result() -> None:
    figure = journal_app._build_daily_result_range_figure(
        best_day="10",
        average_day="-1.25",
        worst_day="-5",
        currency="USD",
    )

    trace = figure.data[0]
    assert list(trace.y) == ["Best day", "Average day", "Worst day"]
    assert list(trace.x) == [10.0, -1.25, -5.0]
    assert list(trace.marker.color) == [
        journal_app._CHART_POSITIVE,
        journal_app._CHART_NEGATIVE,
        journal_app._CHART_NEGATIVE,
    ]
    assert [row[0] for row in trace.customdata] == ["+$10.00", "−$1.25", "−$5.00"]
    assert figure.layout.xaxis.zeroline is True
    assert figure.layout.title.text == "Daily result range"


def test_breakdown_chart_limits_large_sets_and_keeps_the_largest_absolute_results() -> None:
    rows = [
        breakdown(f"Group {index:02d}", str(Decimal(index) * (-1 if index % 2 else 1)))
        for index in range(1, 15)
    ]

    figure, truncated = journal_app._build_breakdown_pnl_figure(
        [(row.label, row) for row in rows],
        currency="USD",
        dimension="Symbol",
    )

    trace = figure.data[0]
    assert truncated is True
    assert len(trace.y) == journal_app._STATISTICS_BREAKDOWN_CHART_LIMIT
    assert set(trace.y) == {f"Group {index:02d}" for index in range(3, 15)}
    assert list(trace.x) == sorted(trace.x, reverse=True)
    assert trace.marker.color[0] == journal_app._CHART_POSITIVE
    assert trace.marker.color[-1] == journal_app._CHART_NEGATIVE
    assert figure.layout.title.text == "Net P&L by symbol"


def test_breakdown_chart_uses_localized_direction_labels_and_title(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "language", lambda: "vi")
    row = breakdown("long", "5", trade_count=3, win_rate="66.666")

    figure, truncated = journal_app._build_breakdown_pnl_figure(
        [(journal_app.tr("Long"), row)],
        currency="USD",
        dimension=journal_app.tr("Direction"),
    )

    assert truncated is False
    assert list(figure.data[0].y) == ["Mua"]
    assert figure.layout.title.text == "P&L ròng theo hướng"


def test_dashboard_metric_tones_only_color_clear_performance_signals() -> None:
    assert journal_app._signed_metric_tone("12.5") == "positive"
    assert journal_app._signed_metric_tone(Decimal("-0.01")) == "negative"
    assert journal_app._signed_metric_tone("0") == "neutral"
    assert journal_app._signed_metric_tone(None) == "neutral"
    assert journal_app._risk_metric_tone(None, 3) == "warning"
    assert journal_app._risk_metric_tone(None, 0) == "neutral"


def test_dashboard_metric_tones_distinguish_coverage_profitability_and_streaks() -> None:
    assert journal_app._r_coverage_metric_tone(4, 4) == "positive"
    assert journal_app._r_coverage_metric_tone(3, 4) == "warning"
    assert journal_app._r_coverage_metric_tone(0, 0) == "neutral"
    assert journal_app._profit_factor_metric_tone("1.01") == "positive"
    assert journal_app._profit_factor_metric_tone("0.99") == "negative"
    assert journal_app._profit_factor_metric_tone("1") == "neutral"
    assert journal_app._profit_factor_metric_tone(None) == "neutral"
    assert journal_app._streak_metric_tone("win") == "positive"
    assert journal_app._streak_metric_tone("loss") == "negative"
    assert journal_app._streak_metric_tone("breakeven") == "neutral"
