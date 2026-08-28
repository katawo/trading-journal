from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from trading_journal.application.dashboard import ConcentrationItem, ConcentrationSide, PerformanceBreakdown
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
    assert [trace.text[0] for trace in figure.data] == ["50.0%", "33.3%", "16.7%"]
    assert [trace.marker.color for trace in figure.data] == [
        journal_app._CHART_POSITIVE,
        journal_app._CHART_NEGATIVE,
        journal_app._CHART_NEUTRAL,
    ]
    assert figure.layout.title.text is None
    assert figure.layout.showlegend is False


def test_performance_history_combines_level_drawdown_and_outcomes_on_one_axis() -> None:
    figure = journal_app._build_performance_history_figure(
        timeline_x=["2026-08-01", "2026-08-02"],
        curve_values=[1000.0, 1010.0],
        curve_customdata=[["2026-08-01", "$1,000.00"], ["2026-08-02", "$1,010.00"]],
        drawdown_values=[0.0, -5.0],
        drawdown_customdata=[["2026-08-01", "$0.00"], ["2026-08-02", "−$5.00"]],
        pnl_x=["2026-08-01", "2026-08-02"],
        pnl_values=[10.0, -5.0],
        pnl_customdata=[["2026-08-01", "+$10.00"], ["2026-08-02", "−$5.00"]],
        curve_title="Account balance curve",
        drawdown_title="Drawdown",
        pnl_title="Daily realized P&L",
        currency="USD",
        curve_is_balance=True,
    )

    assert [trace.type for trace in figure.data] == ["scatter", "scatter", "bar"]
    assert figure.data[0].line.color == journal_app._CHART_POSITIVE
    assert figure.data[1].fill == "tozeroy"
    assert list(figure.data[2].marker.color) == [journal_app._CHART_POSITIVE, journal_app._CHART_NEGATIVE]
    assert figure.layout.xaxis.matches == "x3"
    assert figure.layout.xaxis2.matches == "x3"
    assert figure.layout.height == 560


def test_concentration_chart_keeps_profit_and_empty_loss_visible() -> None:
    profit = ConcentrationSide(
        gross_amount="20",
        group_count=1,
        target_group_count=1,
        target_group_percent="100",
        items=[ConcentrationItem("LT-1", 1, "20", "100", "100")],
    )
    loss = ConcentrationSide("0", 0, 0, None, [])

    figure = journal_app._build_concentration_figure(profit=profit, loss=loss, currency="USD")

    assert [trace.type for trace in figure.data] == ["bar", "scatter", "scatter"]
    assert figure.data[0].marker.color == journal_app._CHART_POSITIVE
    assert figure.data[0].x == (50.0,)
    assert figure.data[0].width == 100.0
    empty_side_placeholder = figure.data[2]
    assert empty_side_placeholder.marker.opacity == 0
    assert empty_side_placeholder.showlegend is False
    assert any("No losing trades" in annotation.text for annotation in figure.layout.annotations)
    assert [annotation.text for annotation in figure.layout.annotations[:2]] == [
        "Profit concentration",
        "Loss concentration",
    ]
    loss_annotation = next(a for a in figure.layout.annotations if "No losing trades" in a.text)
    assert loss_annotation.xref == "x2 domain"
    assert loss_annotation.yref == "y3 domain"
    assert figure.layout.yaxis3.showticklabels is False


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
