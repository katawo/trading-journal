from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from trading_journal.application.dashboard import ConcentrationItem, ConcentrationSide, PerformanceBreakdown
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
        loss_count=max(0, trade_count - 2),
        breakeven_count=0,
        win_rate=win_rate,
        net_pnl=net_pnl,
        total_r=None,
        r_trade_count=0,
        expectancy_r=None,
        profit_factor="1",
    )


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


def test_direction_statistics_format_a_populated_direction() -> None:
    row = breakdown("long", "5", trade_count=3, win_rate="66.666")

    profile_items = journal_app._direction_profile_items(row)
    edge_items = journal_app._direction_edge_items(row, "USD")

    assert profile_items == [
        ("Trades", "3", "info"),
        ("Wins (rate)", "2 (66.7%)", "positive"),
        ("Losses (rate)", "1 (33.3%)", "negative"),
        ("Breakeven", "0", "neutral"),
    ]
    assert edge_items == [
        ("Net P&L", "+$5.00", "positive"),
        ("Total R", "Awaiting risk", "warning"),
        ("Expectancy R", "Awaiting risk", "warning"),
        ("Profit factor", "1.00", "neutral"),
    ]


def test_direction_statistics_keep_an_empty_direction_visible() -> None:
    profile_items = journal_app._direction_profile_items(None)
    edge_items = journal_app._direction_edge_items(None, "USD")

    assert profile_items == [
        ("Trades", "0", "neutral"),
        ("Wins (rate)", "0 (—)", "neutral"),
        ("Losses (rate)", "0 (—)", "neutral"),
        ("Breakeven", "0", "neutral"),
    ]
    assert edge_items == [
        ("Net P&L", "$0.00", "neutral"),
        ("Total R", "—", "neutral"),
        ("Expectancy R", "—", "neutral"),
        ("Profit factor", "—", "neutral"),
    ]


def test_direction_matrices_stack_without_a_fixed_mobile_width(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(journal_app.st, "html", rendered.append)

    journal_app.apply_application_style()

    css = rendered[0]
    mobile_css = css.split("@media (max-width: 760px)", maxsplit=1)[1]
    assert ".dashboard-direction-matrices" in mobile_css
    assert "grid-template-columns: minmax(0, 1fr);" in mobile_css
    assert mobile_css.count("min-width: 0;") >= 2
    assert "border-top: 1px solid" in mobile_css


def test_dashboard_visual_vocabulary_styles_kickers_and_period_metadata(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(journal_app.st, "html", rendered.append)

    journal_app.apply_application_style()

    css = rendered[0]
    period_css = css.split(".dashboard-period {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert ".dashboard-kicker" in css
    assert "letter-spacing: 0.18em;" in css
    assert "text-align: right;" in period_css
    assert "text-transform: uppercase;" in period_css


def test_ongoing_exposure_metrics_use_four_compact_desktop_columns(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(journal_app.st, "html", rendered.append)

    journal_app.apply_application_style()

    css = rendered[0]
    ongoing_css = css.split(".ongoing-exposure-columns {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    divider_css = css.split(".ongoing-exposure-column + .ongoing-exposure-column {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    label_css = css.split(".ongoing-exposure-columns .dashboard-stat-label {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    value_css = css.split(".ongoing-exposure-columns .dashboard-stat-value {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    note_css = css.split(".ongoing-exposure-columns .dashboard-stat-note {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    mobile_css = css.split("@media (max-width: 760px)", maxsplit=1)[1]
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in ongoing_css
    assert "border-left: 1px solid" in divider_css
    assert "font-size: 0.82rem;" in label_css
    assert "font-size: 1.2rem;" in value_css
    assert "font-size: 0.86rem;" in note_css
    assert "grid-template-columns: minmax(0, 1fr);" in mobile_css


def test_ongoing_today_metrics_use_a_responsive_four_card_grid(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(journal_app.st, "html", rendered.append)

    journal_app.apply_application_style()

    css = rendered[0]
    today_css = css.split(".ongoing-today-columns {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    today_divider_css = css.split(".ongoing-today-column + .ongoing-today-column {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    mobile_css = css.split("@media (max-width: 760px)", maxsplit=1)[1]

    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in today_css
    assert "border-left: 1px solid" in today_divider_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in mobile_css


def test_ongoing_metric_notes_support_semantic_risk_tones(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(journal_app.st, "html", rendered.append)

    journal_app.apply_application_style()

    css = rendered[0]
    assert ".dashboard-stat-note.dashboard-stat-tone-positive" in css
    assert ".dashboard-stat-note.dashboard-stat-tone-negative" in css
    assert ".dashboard-stat-note.dashboard-stat-tone-warning" in css


def test_dashboard_metric_tones_only_color_clear_performance_signals() -> None:
    assert journal_app._signed_metric_tone("12.5") == "positive"
    assert journal_app._signed_metric_tone(Decimal("-0.01")) == "negative"
    assert journal_app._signed_metric_tone("0") == "neutral"
    assert journal_app._signed_metric_tone(None) == "neutral"
    assert journal_app._risk_metric_tone(None, 3) == "warning"
    assert journal_app._risk_metric_tone(None, 0) == "neutral"


def test_dashboard_disabled_tone_mutes_immutable_reference_values(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(journal_app.st, "html", rendered.append)

    journal_app.apply_application_style()

    css = rendered[0]
    disabled_css = css.split(".dashboard-stat-tone-disabled {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "color: var(--st-gray-color" in disabled_css
    assert "opacity: 0.65;" in disabled_css


def test_drawdown_values_group_the_percentage_in_parentheses() -> None:
    assert journal_app._format_drawdown_value("68.21", "5.2", "USD") == "−$68.21 (5.2%)"
    assert journal_app._format_drawdown_value("84.37", "6.4", "USD") == "−$84.37 (6.4%)"


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
