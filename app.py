from __future__ import annotations

import os
from html import escape
from importlib.metadata import PackageNotFoundError, version
import tomllib
from uuid import uuid4
from decimal import Decimal
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from trading_journal.application.auto_sync import MT5AutoSyncResult, MT5AutoSyncService
from trading_journal.application.dashboard import DashboardReport, DashboardService, PerformanceBreakdown
from trading_journal.application.framework import FrameworkService
from trading_journal.application.display_time import format_relative_time
from trading_journal.application.import_mt5 import SUPPORTED_SCHEMA_VERSIONS
from trading_journal.application.mt5_paths import default_mt5_export_path, find_mt5_common_files
from trading_journal.desktop import DesktopSyncControl, DesktopSyncStatusStore, desktop_runtime_paths, is_desktop_mode
from trading_journal.infrastructure.sqlite_repository import AccountListItem, JournalDatabaseResetRequiredError, SQLiteJournalRepository
from trading_journal.presentation.framework import (
    _render_framework_rules,
    _render_help_popover,
    _render_risk_policy,
    render_dashboard_coaching_focus,
    render_framework_dashboard,
)
from trading_journal.presentation.branding import TRADE_COMPASS_ICON, render_trade_doctrine
from trading_journal.presentation.global_alert_bubble import GlobalAlertItem, render_global_alert_bubble
from trading_journal.presentation.connection_recovery import render_connection_recovery
from trading_journal.presentation.multiuser_auth import current_username, is_multiuser_mode, render_login_gate, render_logout_control, user_database_path
from trading_journal.presentation.desktop_reset_restart import render_desktop_reset_restart_bridge
from trading_journal.presentation.i18n import (
    LANGUAGES,
    format_relative_time_localized,
    framework_alert_message,
    install_streamlit_translations,
    queue_toast,
    render_pending_toast,
    tr,
)
from trading_journal.presentation.formatting import (
    AccentMetricTone,
    currency_decimal_places,
    currency_prefix,
    format_count,
    format_currency,
    format_number,
    format_percent,
    format_r,
)
from trading_journal.presentation.trade_tags import direction_tag, outcome_tag


_AUTO_SYNC_INTERVAL_SECONDS = 5
_FRESHNESS_INTERVAL_SECONDS = 5
_ANALYTICS_CACHE_TTL_SECONDS = 15
_CHART_POSITIVE = "#0e9163"
_CHART_NEGATIVE = "#c73545"
_CHART_NEUTRAL = "#7a828e"
_STATISTICS_BREAKDOWN_CHART_LIMIT = 12

_DashboardMetricTone = AccentMetricTone


def application_version() -> str:
    """Return the installed application version, including in desktop bundles."""
    source_manifest = Path(__file__).with_name("pyproject.toml")
    if source_manifest.is_file():
        with source_manifest.open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    return version("trade-compass")


def supported_mt5_schema_versions() -> str:
    return ", ".join(str(item) for item in sorted(SUPPORTED_SCHEMA_VERSIONS))


def format_currency_caption(value: str | Decimal, currency: str, *, signed: bool = True) -> str:
    """Format currency safely for Streamlit text elements that parse Markdown."""
    return format_currency(value, currency, signed=signed).replace("$", r"\$")


def format_account_label(account: AccountListItem) -> str:
    return f"{account.display_name} · {account.login} · {account.broker_server}"


def _signed_metric_tone(
    value: str | Decimal | int | None,
    *,
    missing: _DashboardMetricTone = "neutral",
) -> _DashboardMetricTone:
    """Map an unambiguously signed result to its dashboard presentation tone."""
    if value is None:
        return missing
    amount = Decimal(str(value))
    if amount > 0:
        return "positive"
    if amount < 0:
        return "negative"
    return "neutral"


def _presence_metric_tone(count: int, tone: _DashboardMetricTone) -> _DashboardMetricTone:
    return tone if count > 0 else "neutral"


def _profit_factor_metric_tone(value: str | None) -> _DashboardMetricTone:
    if value is None:
        return "neutral"
    factor = Decimal(value)
    if factor > 1:
        return "positive"
    if factor < 1:
        return "negative"
    return "neutral"


def _risk_metric_tone(value: str | None, trade_count: int) -> _DashboardMetricTone:
    if value is None:
        return "warning" if trade_count else "neutral"
    return _signed_metric_tone(value)


def _r_coverage_metric_tone(covered: int, total: int) -> _DashboardMetricTone:
    if total == 0:
        return "neutral"
    return "positive" if covered == total else "warning"


def _streak_metric_tone(outcome: str | None) -> _DashboardMetricTone:
    return {"win": "positive", "loss": "negative", "breakeven": "neutral", None: "neutral"}[outcome]


def _dashboard_stat_html(label: str, value: str, tone: _DashboardMetricTone) -> str:
    return (
        '<div class="dashboard-stat">'
        f'<div class="dashboard-stat-label">{escape(label)}</div>'
        f'<div class="dashboard-stat-value dashboard-stat-tone-{tone}">{escape(value)}</div>'
        "</div>"
    )


def _render_stat_grid(
    items: list[tuple[str, str, _DashboardMetricTone]],
    *,
    class_name: str = "",
) -> None:
    """Render label/value pairs as a dense grid instead of individual metric cards."""
    cells = "".join(_dashboard_stat_html(label, value, tone) for label, value, tone in items)
    classes = "dashboard-stat-grid" + (f" {class_name}" if class_name else "")
    st.markdown(f'<div class="{classes}">{cells}</div>', unsafe_allow_html=True)


def style_chart(figure: go.Figure, *, yaxis_title: str, currency: str | None = None) -> go.Figure:
    """Keep data semantics while Streamlit supplies the active chart theme.

    The browser can switch Streamlit's Light/Dark theme without a Python rerun.
    Avoid baking a server-side palette into Plotly so that switch remains
    coherent with the rest of the application.
    """
    layout_update = dict(
        height=330,
        margin=dict(l=12, r=12, t=42, b=12),
        hovermode="x unified",
        showlegend=False,
    )
    if figure.layout.title.text:
        # Position an already-set title; don't create an empty one (Plotly.js
        # renders a title object with no `text` as the literal string "undefined").
        layout_update["title"] = dict(x=0.02, y=0.96)
    figure.update_layout(**layout_update)
    figure.update_xaxes(showgrid=False, zeroline=False)
    yaxis = {"title": yaxis_title, "zeroline": True}
    if currency is not None:
        yaxis.update(tickprefix=currency_prefix(currency), tickformat=f",.{currency_decimal_places(currency)}f")
    figure.update_yaxes(**yaxis)
    return figure


def _concentration_summary(side, *, positive: bool) -> str:
    if not side.items:
        outcome = tr("profitable") if positive else tr("losing")
        return tr("No {outcome} logical trades are available for this view.", outcome=outcome)
    target = side.items[side.target_group_count - 1]
    gross_label = tr("gross profit") if positive else tr("gross loss")
    return tr(
        "Top {count} of {total} groups ({group_percent}) account for {share} of {gross_label}.",
        count=side.target_group_count,
        total=side.group_count,
        group_percent=format_percent(side.target_group_percent),
        share=format_percent(target.cumulative_share_percent),
        gross_label=gross_label,
    )


def _build_concentration_figure(*, profit, loss, currency: str) -> go.Figure:
    """Show both Pareto sides in one figure so neither outcome is hidden."""
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=(tr("Profit concentration"), tr("Loss concentration")),
        specs=[[{"secondary_y": True}, {"secondary_y": True}]],
        horizontal_spacing=0.12,
    )
    for column, side, positive in ((1, profit, True), (2, loss, False)):
        if side.items:
            count = len(side.items)
            bin_width = 100 / count
            rank_percent = [(index + 0.5) * bin_width for index in range(count)]
            amount_labels = [format_currency(item.amount, currency, signed=False) for item in side.items]
            figure.add_trace(
                go.Bar(
                    x=rank_percent,
                    y=[float(item.amount) for item in side.items],
                    width=bin_width,
                    name=tr("Gross profit" if positive else "Gross loss"),
                    marker_color=_CHART_POSITIVE if positive else _CHART_NEGATIVE,
                    marker_line_width=0,
                    customdata=list(
                        zip(
                            [item.label for item in side.items],
                            amount_labels,
                            [format_percent(item.share_percent) for item in side.items],
                            [item.trade_count for item in side.items],
                            strict=True,
                        )
                    ),
                    hovertemplate=(
                        "%{customdata[0]}<br><b>%{customdata[1]}</b><br>%{customdata[2]} "
                        + tr("of total")
                        + f"<br>%{{customdata[3]}} {tr('contributing logical trades')}<extra></extra>"
                    ),
                ),
                row=1,
                col=column,
                secondary_y=False,
            )
            figure.add_trace(
                go.Scatter(
                    x=rank_percent,
                    y=[float(item.cumulative_share_percent) for item in side.items],
                    name=tr("Cumulative share"),
                    mode="lines+markers",
                    line=dict(color="#1e6ecb", width=2),
                    marker=dict(color="#1e6ecb", size=5),
                    hovertemplate=f"%{{x:.0f}}%<br><b>%{{y:.1f}}% {tr('cumulative')}</b><extra></extra>",
                ),
                row=1,
                col=column,
                secondary_y=True,
            )
        else:
            # An empty-side annotation needs a real trace bound to this subplot's axes,
            # otherwise Plotly silently resolves its "domain" xref/yref against the
            # first subplot instead, misplacing the message entirely.
            figure.add_trace(
                go.Scatter(x=[50], y=[0], mode="markers", marker=dict(opacity=0), showlegend=False, hoverinfo="skip"),
                row=1,
                col=column,
                secondary_y=False,
            )
            outcome = tr("profitable") if positive else tr("losing")
            figure.add_annotation(
                text=tr("No {outcome} trades", outcome=outcome),
                x=0.5,
                y=0.5,
                xref="x domain" if column == 1 else "x2 domain",
                yref="y domain" if column == 1 else "y3 domain",
                showarrow=False,
                font=dict(color=_CHART_NEUTRAL),
            )
        figure.update_yaxes(
            tickprefix=currency_prefix(currency),
            tickformat=f",.{currency_decimal_places(currency)}f",
            showticklabels=bool(side.items),
            row=1,
            col=column,
            secondary_y=False,
        )
        figure.update_yaxes(
            range=[0, 100],
            ticksuffix="%",
            showgrid=False,
            showticklabels=bool(side.items),
            row=1,
            col=column,
            secondary_y=True,
        )
        figure.update_xaxes(
            title=tr("% of profitable trades") if positive else tr("% of losing trades"),
            range=[0, 100],
            dtick=25,
            ticksuffix="%",
            showgrid=False,
            row=1,
            col=column,
        )
    figure.update_layout(
        height=320,
        margin=dict(l=10, r=10, t=54, b=12),
        showlegend=False,
        hovermode="closest",
    )
    return figure


def _build_outcome_mix_figure(*, win_count: int, loss_count: int, breakeven_count: int) -> go.Figure:
    outcomes = [
        (tr("Wins"), win_count, _CHART_POSITIVE),
        (tr("Losses"), loss_count, _CHART_NEGATIVE),
        (tr("Breakevens"), breakeven_count, _CHART_NEUTRAL),
    ]
    total = sum(count for _, count, _ in outcomes)
    figure = go.Figure()
    for label, count, colour in outcomes:
        share = Decimal(count * 100) / Decimal(total)
        figure.add_trace(
            go.Bar(
                name=label,
                x=[float(share)],
                y=[tr("Closed logical trades")],
                orientation="h",
                marker_color=colour,
                marker_line_width=0,
                customdata=[[format_count(count), format_percent(share)]],
                text=[format_percent(share) if count else ""],
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate="%{fullData.name}<br><b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>",
            )
        )
    figure.update_layout(
        height=105,
        margin=dict(l=4, r=4, t=4, b=24),
        barmode="stack",
        showlegend=False,
        hovermode="closest",
    )
    figure.update_xaxes(
        range=[0, 100],
        tickvals=[0, 25, 50, 75, 100],
        ticksuffix="%",
        showgrid=False,
        zeroline=False,
    )
    figure.update_yaxes(showgrid=False, zeroline=False, showticklabels=False, title=None)
    return figure


def _build_performance_history_figure(
    *,
    timeline_x,
    curve_values,
    curve_customdata,
    drawdown_values,
    drawdown_customdata,
    pnl_x,
    pnl_values,
    pnl_customdata,
    curve_title: str,
    drawdown_title: str,
    pnl_title: str,
    currency: str,
    curve_is_balance: bool,
) -> go.Figure:
    """Build one shared-axis history view for level, risk, and realized outcomes."""
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.055,
        row_heights=[0.48, 0.24, 0.28],
        subplot_titles=(curve_title, drawdown_title, pnl_title),
    )
    figure.add_trace(
        go.Scatter(
            x=timeline_x,
            y=curve_values,
            customdata=curve_customdata,
            mode="lines",
            line=dict(color=_CHART_POSITIVE, width=2.5),
            fill=None if curve_is_balance else "tozeroy",
            fillcolor="rgba(14, 145, 99, 0.12)" if not curve_is_balance else None,
            hovertemplate="%{customdata[0]}<br><b>%{customdata[1]}</b><extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=timeline_x,
            y=drawdown_values,
            customdata=drawdown_customdata,
            mode="lines",
            line=dict(color=_CHART_NEGATIVE, width=2),
            fill="tozeroy",
            fillcolor="rgba(199, 53, 69, 0.16)",
            hovertemplate="%{customdata[0]}<br><b>%{customdata[1]}</b><extra></extra>",
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=pnl_x,
            y=pnl_values,
            customdata=pnl_customdata,
            marker_color=[_CHART_POSITIVE if value >= 0 else _CHART_NEGATIVE for value in pnl_values],
            marker_line_width=0,
            hovertemplate="%{customdata[0]}<br><b>%{customdata[1]}</b><extra></extra>",
        ),
        row=3,
        col=1,
    )
    figure.update_layout(
        height=560,
        margin=dict(l=12, r=12, t=44, b=12),
        showlegend=False,
        hovermode="x unified",
        bargap=0.16,
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    for row in (1, 2, 3):
        figure.update_yaxes(
            tickprefix=currency_prefix(currency),
            tickformat=f",.{currency_decimal_places(currency)}f",
            zeroline=True,
            row=row,
            col=1,
        )
    return figure


def _build_breakdown_pnl_figure(
    items: list[tuple[str, PerformanceBreakdown]],
    *,
    currency: str,
    dimension: str,
) -> tuple[go.Figure, bool]:
    truncated = len(items) > _STATISTICS_BREAKDOWN_CHART_LIMIT
    selected = sorted(
        items,
        key=lambda item: (-abs(Decimal(item[1].net_pnl)), item[0].casefold()),
    )[:_STATISTICS_BREAKDOWN_CHART_LIMIT]
    selected.sort(key=lambda item: (-Decimal(item[1].net_pnl), item[0].casefold()))
    labels = [label for label, _ in selected]
    values = [Decimal(row.net_pnl) for _, row in selected]
    colours = [_CHART_POSITIVE if value > 0 else _CHART_NEGATIVE if value < 0 else _CHART_NEUTRAL for value in values]
    customdata = [
        [format_currency(value, currency), format_count(row.trade_count), format_percent(row.win_rate)]
        for (_, row), value in zip(selected, values, strict=True)
    ]
    figure = go.Figure(
        go.Bar(
            x=[float(value) for value in values],
            y=labels,
            orientation="h",
            marker_color=colours,
            marker_line_width=0,
            customdata=customdata,
            hovertemplate=(
                "%{y}<br><b>%{customdata[0]}</b>"
                f"<br>{tr('Trades')}: %{{customdata[1]}}"
                f"<br>{tr('Win rate')}: %{{customdata[2]}}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        title=dict(text=tr("Net P&L by {dimension}", dimension=dimension.lower()), x=0.02, y=0.97),
        height=min(520, max(220, len(selected) * 34 + 115)),
        margin=dict(l=12, r=12, t=52, b=12),
        showlegend=False,
        hovermode="closest",
    )
    figure.update_xaxes(
        title=f"{tr('Net P&L')} ({currency})",
        tickprefix=currency_prefix(currency),
        tickformat=f",.{currency_decimal_places(currency)}f",
        zeroline=True,
        zerolinewidth=1,
    )
    figure.update_yaxes(showgrid=False, zeroline=False, autorange="reversed")
    return figure, truncated


def _render_dashboard_statistics(report: DashboardReport, currency: str) -> None:
    """Render one dense outcome surface instead of stacked statistic groups."""
    with st.container(border=False):
        st.markdown(f"#### {tr('Trade outcomes')}")
        st.caption(tr("Outcome statistics use the all-time closed logical-trade record."))
        outcome_columns = st.container(key="dashboard-outcome-columns")
        profit, loss, outcome = outcome_columns.columns([1, 1, 1.35], gap="medium")
        with profit:
            st.markdown(f'<div class="dashboard-stat-column-head">{tr("Profit")}</div>', unsafe_allow_html=True)
            _render_stat_grid([
                (tr("Gross profit"), format_currency(report.gross_profit, currency, signed=False), _signed_metric_tone(report.gross_profit)),
                (
                    tr("Average win"),
                    tr("No wins") if report.average_win is None else format_currency(report.average_win, currency),
                    "neutral" if report.average_win is None else "positive",
                ),
                (
                    tr("Wins"),
                    f"{format_count(report.win_count)} ({format_percent(Decimal(report.win_count * 100) / Decimal(report.trade_count))})",
                    _presence_metric_tone(report.win_count, "positive"),
                ),
                (
                    tr("Best day"),
                    "—" if report.best_day is None else format_currency(report.best_day, currency),
                    _signed_metric_tone(report.best_day),
                ),
                (
                    tr("Longest win streak"),
                    format_count(report.longest_win_streak),
                    _presence_metric_tone(report.longest_win_streak, "positive"),
                ),
            ], class_name="dashboard-stat-list")
        with loss:
            st.markdown(f'<div class="dashboard-stat-column-head">{tr("Loss")}</div>', unsafe_allow_html=True)
            _render_stat_grid([
                (tr("Gross loss"), format_currency(-Decimal(report.gross_loss), currency), _presence_metric_tone(report.loss_count, "negative")),
                (
                    tr("Average loss"),
                    tr("No losses") if report.average_loss is None else format_currency(report.average_loss, currency),
                    "neutral" if report.average_loss is None else "negative",
                ),
                (
                    tr("Losses"),
                    f"{format_count(report.loss_count)} ({format_percent(Decimal(report.loss_count * 100) / Decimal(report.trade_count))})",
                    _presence_metric_tone(report.loss_count, "negative"),
                ),
                (
                    tr("Worst day"),
                    "—" if report.worst_day is None else format_currency(report.worst_day, currency),
                    _signed_metric_tone(report.worst_day),
                ),
                (
                    tr("Longest loss streak"),
                    format_count(report.longest_loss_streak),
                    _presence_metric_tone(report.longest_loss_streak, "negative"),
                ),
            ], class_name="dashboard-stat-list")
        with outcome:
            st.markdown(f'<div class="dashboard-stat-column-head">{tr("Outcome mix")}</div>', unsafe_allow_html=True)
            if report.trade_count:
                st.plotly_chart(
                    _build_outcome_mix_figure(
                        win_count=report.win_count,
                        loss_count=report.loss_count,
                        breakeven_count=report.breakeven_count,
                    ),
                    width="stretch",
                    config={"displayModeBar": False},
                    key="dashboard-statistics-outcome-mix",
                )
            _render_stat_grid([
                (tr("Win rate"), format_percent(report.win_rate), "info"),
                (
                    tr("Expectancy R"),
                    tr("Awaiting risk") if report.expectancy_r is None else format_r(report.expectancy_r),
                    _risk_metric_tone(report.expectancy_r, report.trade_count),
                ),
                (tr("Payoff ratio"), "—" if report.payoff_ratio is None else format_number(report.payoff_ratio, 2), "info"),
                (
                    tr("R coverage"),
                    format_percent(Decimal(report.r_trade_count * 100) / Decimal(report.trade_count)),
                    _r_coverage_metric_tone(report.r_trade_count, report.trade_count),
                ),
            ], class_name="dashboard-outcome-stats")

        current_streak = "—"
        if report.current_streak_outcome is not None:
            streak_outcome = {"win": "Win", "loss": "Loss", "breakeven": "Breakeven"}[report.current_streak_outcome]
            current_streak = f"{format_count(report.current_streak_count)} · {tr(streak_outcome)}"
        _render_stat_grid(
            [
                (tr("Active trading days"), format_count(report.active_day_count), "info"),
                (
                    tr("Profitable days"),
                    f"{format_count(report.profitable_day_count)}/{format_count(report.active_day_count)} ({format_percent(report.profitable_day_rate)})",
                    "info",
                ),
                (
                    tr("Average day"),
                    "—" if report.average_day is None else format_currency(report.average_day, currency),
                    _signed_metric_tone(report.average_day),
                ),
                (
                    tr("Recovery factor"),
                    tr("No drawdown") if report.recovery_factor is None else format_number(report.recovery_factor, 2),
                    "info",
                ),
                (tr("Current streak"), current_streak, _streak_metric_tone(report.current_streak_outcome)),
            ],
            class_name="dashboard-outcome-footer",
        )

        st.markdown(f"**{tr('Breakdown view')}**")
        breakdown_view = st.segmented_control(
            tr("Breakdown view"),
            [tr("Direction"), tr("Symbol")],
            default=tr("Direction"),
            required=True,
            width="content",
            key="dashboard-statistics-breakdown",
            label_visibility="collapsed",
        )
        rows = report.by_symbol if breakdown_view == tr("Symbol") else report.by_direction
        labels = [item.label for item in rows]
        if breakdown_view == tr("Direction"):
            labels = [tr(direction_tag(item.label).label) for item in rows]
        breakdown_columns = st.container(key="dashboard-breakdown-columns")
        breakdown_chart_column, breakdown_table_column = breakdown_columns.columns([1, 1], gap="medium")
        with breakdown_chart_column:
            if rows:
                breakdown_figure, truncated = _build_breakdown_pnl_figure(
                    list(zip(labels, rows, strict=True)),
                    currency=currency,
                    dimension=breakdown_view,
                )
                st.plotly_chart(
                    breakdown_figure,
                    width="stretch",
                    config={"displayModeBar": False},
                    key="dashboard-statistics-breakdown-pnl",
                )
                if truncated:
                    st.caption(
                        tr(
                            "Chart shows the {count} groups with the largest absolute net P&L; the table includes every group.",
                            count=format_count(_STATISTICS_BREAKDOWN_CHART_LIMIT),
                        )
                    )
            else:
                st.caption(tr("Complete logical trades will populate the breakdown chart."))
        with breakdown_table_column:
            breakdown_frame, breakdown_config = _dashboard_breakdown_frame(rows, labels, currency)
            st.markdown(f"**{tr('Breakdown detail')}**")
            st.dataframe(
                breakdown_frame,
                hide_index=True,
                width="stretch",
                height=min(250, 42 + max(1, len(breakdown_frame)) * 35),
                column_config=breakdown_config,
            )


def _dashboard_breakdown_frame(rows, labels: list[str], currency: str) -> tuple[pd.DataFrame, dict[str, object]]:
    group_column = tr("Group")
    trades_column = tr("Trades")
    win_rate_column = tr("Win rate")
    pnl_column = f"{tr('Net P&L')} ({currency})"
    total_r_column = tr("Total R")
    expectancy_r_column = tr("Expectancy R")
    profit_factor_column = tr("Profit factor")
    frame = pd.DataFrame(
        {
            group_column: labels,
            trades_column: pd.Series([item.trade_count for item in rows], dtype="int64"),
            tr("W-L-B"): [f"{item.win_count}-{item.loss_count}-{item.breakeven_count}" for item in rows],
            win_rate_column: pd.Series([float(item.win_rate) for item in rows], dtype="float64"),
            pnl_column: pd.Series([float(item.net_pnl) for item in rows], dtype="float64"),
            total_r_column: pd.Series([None if item.total_r is None else float(item.total_r) for item in rows], dtype="float64"),
            expectancy_r_column: pd.Series([None if item.expectancy_r is None else float(item.expectancy_r) for item in rows], dtype="float64"),
            profit_factor_column: pd.Series([None if item.profit_factor is None else float(item.profit_factor) for item in rows], dtype="float64"),
            tr("R coverage"): [f"{item.r_trade_count}/{item.trade_count}" for item in rows],
        }
    )
    config = {
        group_column: st.column_config.TextColumn(pinned=True),
        trades_column: st.column_config.NumberColumn(format="%d"),
        win_rate_column: st.column_config.NumberColumn(format="%.1f%%"),
        pnl_column: st.column_config.NumberColumn(format=f"%+.{currency_decimal_places(currency)}f"),
        total_r_column: st.column_config.NumberColumn(format="%+.2fR"),
        expectancy_r_column: st.column_config.NumberColumn(format="%+.2fR"),
        profit_factor_column: st.column_config.NumberColumn(format="%.2f"),
    }
    return frame, config


def apply_application_style() -> None:
    st.html("""
        <style>
        [data-testid="stAppViewContainer"] > .main .block-container { max-width: 1480px; padding-top: 2.6rem; padding-bottom: 4rem; }
        [data-testid="stSidebar"] [data-testid="stSidebarNav"] { padding-top: 1.75rem; padding-bottom: 3.4rem; }
        [data-testid="stSidebar"] [data-testid="stSidebarNav"] a { border-radius: 8px; margin: 0.12rem 0.75rem; padding: 0.48rem 0.65rem; font-weight: 600; }
        h1, h2, h3 { letter-spacing: -0.03em; }
        h1 { font-size: 2.8rem !important; margin-bottom: 0.15rem !important; }
        h2 { margin-top: 1.35rem !important; }
        .stButton > button { border-radius: 6px; font-weight: 650; }
        div.st-key-trade-compass-brand { flex-direction: row !important; align-items: center !important; gap: 0.65rem; }
        div.st-key-trade-compass-brand [data-testid="stImage"] { flex: 0 0 auto; }
        div.st-key-trade-compass-brand h1 { margin: 0 !important; }
        div[class*="st-key-dashboard-metric-"] {
            flex: 1 1 9rem;
            min-width: min(9rem, 100%);
        }
        div[class*="st-key-dashboard-metric-"] [data-testid="stMetric"] {
            box-shadow: inset 3px 0 0 var(--dashboard-metric-accent);
        }
        div[class*="st-key-dashboard-metric-positive-"] {
            --dashboard-metric-accent: var(--st-green-color, #0e9163);
        }
        div[class*="st-key-dashboard-metric-negative-"] {
            --dashboard-metric-accent: var(--st-red-color, #c73545);
        }
        div[class*="st-key-dashboard-metric-warning-"] {
            --dashboard-metric-accent: var(--st-orange-color, #a65f00);
        }
        div[class*="st-key-dashboard-metric-info-"] {
            --dashboard-metric-accent: var(--st-blue-color, #1666a5);
        }
        div[class*="st-key-dashboard-metric-neutral-"] {
            --dashboard-metric-accent: var(--st-gray-color, #667168);
        }
        .dashboard-stat-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
            gap: 0.5rem 1.5rem;
            padding: 0.5rem 0;
        }
        .dashboard-stat-grid + .dashboard-stat-grid {
            border-top: 1px solid var(--st-border-color, #c8d0c8);
            margin-top: 0.15rem;
            padding-top: 0.65rem;
        }
        .dashboard-stat-label {
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--st-gray-color, #667168);
        }
        .dashboard-stat-value {
            font-size: 1rem;
            font-weight: 600;
            margin-top: 0.1rem;
            font-variant-numeric: tabular-nums;
        }
        .dashboard-stat-tone-positive { color: var(--st-green-color, #0e9163); }
        .dashboard-stat-tone-negative { color: var(--st-red-color, #c73545); }
        .dashboard-stat-tone-warning { color: var(--st-orange-color, #a65f00); }
        .dashboard-stat-tone-info { color: var(--st-blue-color, #1666a5); }
        .dashboard-stat-tone-neutral { color: inherit; }
        .dashboard-stat-list {
            display: block;
            padding-top: 0.35rem;
        }
        .dashboard-stat-list .dashboard-stat,
        .dashboard-outcome-stats .dashboard-stat {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: baseline;
            gap: 0.75rem;
            min-height: 1.6rem;
        }
        .dashboard-stat-list .dashboard-stat-value,
        .dashboard-outcome-stats .dashboard-stat-value {
            margin-top: 0;
            text-align: right;
        }
        .dashboard-outcome-stats {
            grid-template-columns: repeat(2, minmax(7rem, 1fr));
            gap: 0.35rem 1.35rem;
            padding: 0.1rem 0 0;
        }
        .dashboard-outcome-footer {
            grid-template-columns: repeat(5, minmax(7.5rem, 1fr));
            border-top: 1px solid var(--st-border-color, #c8d0c8);
            margin-top: 0.35rem;
            padding: 0.65rem 0 0;
        }
        div.st-key-dashboard-outcome-columns [data-testid="stColumn"] + [data-testid="stColumn"],
        div.st-key-dashboard-breakdown-columns [data-testid="stColumn"] + [data-testid="stColumn"],
        div.st-key-dashboard-process-risk-columns [data-testid="stColumn"] + [data-testid="stColumn"] {
            border-left: 1px solid var(--st-border-color, #c8d0c8);
            padding-left: 1rem;
        }
        .dashboard-stat-column-head {
            font-size: 0.66rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            border-bottom: 1px solid var(--st-border-color, #c8d0c8);
            padding-bottom: 0.35rem;
            margin-bottom: 0.15rem;
        }
        .dashboard-stat-note {
            color: var(--st-gray-color, #667168);
            font-size: 0.72rem;
            line-height: 1.25;
            margin-top: 0.1rem;
        }
        .dashboard-framework-stats {
            grid-template-columns: repeat(4, minmax(7rem, 1fr));
            align-content: center;
            min-height: 180px;
        }
        .dashboard-framework-stats .dashboard-stat {
            border: 1px solid var(--st-border-color, #c8d0c8);
            border-radius: 0.5rem;
            padding: 0.6rem 0.75rem;
            text-align: center;
        }
        .dashboard-pillar-empty {
            display: grid;
            align-content: center;
            gap: 1.15rem;
            min-height: 180px;
            padding: 0.75rem 0;
        }
        .dashboard-pillar-empty-row {
            display: grid;
            grid-template-columns: minmax(7.5rem, 0.8fr) minmax(8rem, 1.5fr) auto;
            align-items: center;
            gap: 0.8rem;
        }
        .dashboard-pillar-empty-label,
        .dashboard-pillar-empty-value {
            font-size: 0.78rem;
            color: var(--st-gray-color, #667168);
        }
        .dashboard-pillar-empty-track {
            height: 0.45rem;
            border-radius: 999px;
            background: color-mix(in srgb, var(--st-gray-color, #667168) 18%, transparent);
        }
        @media (max-width: 760px) {
            .dashboard-stat-grid,
            .dashboard-framework-stats {
                grid-template-columns: repeat(2, minmax(7.5rem, 1fr));
            }
            .dashboard-stat-list {
                display: block;
            }
            .dashboard-outcome-stats {
                grid-template-columns: repeat(2, minmax(7.5rem, 1fr));
            }
            .dashboard-outcome-footer {
                grid-template-columns: repeat(2, minmax(7.5rem, 1fr));
            }
            div.st-key-dashboard-outcome-columns [data-testid="stColumn"] + [data-testid="stColumn"],
            div.st-key-dashboard-breakdown-columns [data-testid="stColumn"] + [data-testid="stColumn"],
            div.st-key-dashboard-process-risk-columns [data-testid="stColumn"] + [data-testid="stColumn"] {
                border-left: 0;
                padding-left: 0;
            }
            .dashboard-pillar-empty-row {
                grid-template-columns: 1fr;
                gap: 0.35rem;
            }
        }
        /* st.tabs' row doesn't wrap on narrow viewports and clips the last label;
           make the scroll it already supports visible so it reads as scrollable
           instead of just cut off. */
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: thin;
        }
        [data-testid="stSidebar"] .trade-compass-build-info {
            position: fixed;
            bottom: 0;
            left: 0;
            z-index: 1;
            box-sizing: border-box;
            width: var(--st-sidebar-width, 21rem);
            padding: 0.65rem 1rem;
            border-top: 1px solid var(--st-border-color, #c8d0c8);
            background: var(--secondary-background-color);
            color: var(--text-color);
            font-size: 0.78rem;
            opacity: 0.8;
        }
        </style>
        """)


def render_build_info() -> None:
    st.sidebar.html(
        "<div class=\"trade-compass-build-info\">"
        f"App v{application_version()} · MT5 schema v{supported_mt5_schema_versions()}"
        "</div>"
    )


@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=32, show_spinner=False)
def _cached_global_framework_alerts(database_path: str, database_change_token: tuple[int, int, int, int]) -> tuple[tuple[AccountListItem, object], ...]:
    """Cache cross-account analytics until the local database changes."""
    del database_change_token
    repo = SQLiteJournalRepository(database_path)
    return tuple(
        (account, alert)
        for account in repo.list_mt5_accounts()
        for alert in FrameworkService(repo).framework_alerts(account.id)
    )


@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=32, show_spinner=False)
def _cached_review_queue_count(database_path: str, database_change_token: tuple[int, int, int, int], account_id: int) -> int:
    """Cache the active account's pending-review count until the local database changes."""
    del database_change_token
    repo = SQLiteJournalRepository(database_path)
    return sum(score.review_kind == "needs_approval" for score in FrameworkService(repo).trade_process_scores(account_id))


@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=32, show_spinner=False)
def _cached_focus_ready_to_evaluate(database_path: str, database_change_token: tuple[int, int, int, int], account_id: int) -> bool:
    """Cache whether the active account's coaching focus is ready to resolve."""
    del database_change_token
    repo = SQLiteJournalRepository(database_path)
    _, progress = FrameworkService(repo).focus_progress(account_id)
    return bool(progress and progress.ready_to_evaluate)


def _database_change_token(database_path: Path) -> tuple[int, int, int, int]:
    """Track committed SQLite changes in both the main file and its WAL."""

    values: list[int] = []
    for path in (database_path, Path(f"{database_path}-wal")):
        try:
            stat = path.stat()
        except FileNotFoundError:
            values.extend((0, 0))
        else:
            values.extend((stat.st_mtime_ns, stat.st_size))
    return values[0], values[1], values[2], values[3]


def render_global_framework_alert_bubble(repo: SQLiteJournalRepository) -> None:
    """Persistent cross-account warning/critical alert entry point."""
    severity_order = {"critical": 0, "warning": 1}
    database_path = getattr(repo, "database_path", None)
    source = (
        _cached_global_framework_alerts(str(database_path), _database_change_token(database_path))
        if database_path is not None
        else tuple(
            (account, alert)
            for account in repo.list_mt5_accounts()
            for alert in FrameworkService(repo).framework_alerts(account.id)
        )
    )
    alerts = [(account, alert) for account, alert in source if alert.severity in severity_order]
    if not alerts:
        return
    alerts.sort(key=lambda item: (severity_order[item[1].severity], item[0].display_name, item[1].code))
    critical = sum(alert.severity == "critical" for _, alert in alerts)
    warnings = len(alerts) - critical
    label = (
        tr("{critical} critical · {warnings} warning{plural}", critical=critical, warnings=warnings, plural="s" if warnings != 1 else "")
        if critical
        else tr("{warnings} warning{plural}", warnings=warnings, plural="s" if warnings != 1 else "")
    )
    bubble_alerts: list[GlobalAlertItem] = [
        {
            "account_name": account.display_name,
            "code": alert.code,
            "message": framework_alert_message(alert.code, alert.message),
            "severity": alert.severity,
        }
        for account, alert in alerts
    ]
    render_global_alert_bubble(
        alerts=bubble_alerts,
        label=label,
        has_critical=bool(critical),
        panel_title=tr("Active alerts"),
        drag_hint=tr("Drag to move. Click to view active alerts."),
    )


# Bounds how many distinct SQLite engines (one per multiuser database_path) stay
# open at once; irrelevant for desktop/single-user web mode, which only ever hit
# one path, but multiuser mode would otherwise keep growing this forever as more
# users log in over the life of the process.
_REPOSITORY_CACHE_MAX_ENTRIES = 64


@st.cache_resource(show_spinner=False, max_entries=_REPOSITORY_CACHE_MAX_ENTRIES)
def _cached_repository(database_path: str) -> SQLiteJournalRepository:
    repo = SQLiteJournalRepository(database_path)
    repo.initialize()
    return repo


def repository() -> SQLiteJournalRepository:
    if is_multiuser_mode():
        username = current_username()
        if username is None:
            raise RuntimeError("repository() called before a multiuser login succeeded")
        database_path = user_database_path(username).resolve()
    else:
        database_path = Path(os.environ.get("TRADING_JOURNAL_DB", "data/trading_journal.db")).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return _cached_repository(str(database_path))


@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=128, show_spinner=False)
def _cached_dashboard_report(
    database_path: str,
    database_change_token: tuple[int, int, int, int],
    account_id: int,
):
    del database_change_token
    return DashboardService(SQLiteJournalRepository(database_path)).build_report(account_id=account_id)


def build_dashboard_report(repo: SQLiteJournalRepository, *, account_id: int):
    return _cached_dashboard_report(
        str(repo.database_path),
        _database_change_token(repo.database_path),
        account_id,
    )


def _render_sync_results(results: list[MT5AutoSyncResult], *, notice_key: str | None = None) -> bool:
    """Render sync state and report whether a newly imported batch needs an app rerun."""

    st.session_state["auto_sync_results"] = results
    render_sync_failures(results, prefix="MT5 auto-sync needs attention")
    imported = [item for item in results if item.status == "imported"]
    if not imported or notice_key is None:
        return False
    if st.session_state.get("auto_sync_notice_key") == notice_key:
        return False
    st.session_state["auto_sync_notice_key"] = notice_key
    created = sum(item.created_count for item in imported)
    updated = sum(item.updated_count for item in imported)
    st.session_state["auto_sync_notice"] = f"Auto-imported {created} created and {updated} updated MT5 position(s)."
    return True


@st.fragment(run_every=_AUTO_SYNC_INTERVAL_SECONDS)
def _monitor_local_mt5_exports(repo: SQLiteJournalRepository) -> None:
    results = MT5AutoSyncService(repo).sync_configured_exports()
    notice_key = ";".join(
        f"{item.account_login}:{item.broker_server}:{item.export_updated_at.isoformat() if item.export_updated_at else ''}"
        for item in results
        if item.status == "imported"
    )
    if _render_sync_results(results, notice_key=notice_key or None):
        st.rerun()


@st.fragment(run_every=_FRESHNESS_INTERVAL_SECONDS)
def _monitor_desktop_mt5_exports() -> None:
    paths = desktop_runtime_paths()
    status = DesktopSyncStatusStore(paths.sync_status_path)
    results = status.results()
    if _render_sync_results(results, notice_key=status.last_import_at().isoformat() if status.last_import_at() else None):
        st.rerun()
    if error := status.worker_error():
        st.error(f"MT5 desktop sync needs attention: {error}")


def monitor_mt5_exports(repo: SQLiteJournalRepository) -> None:
    """Render live MT5 state without giving desktop mode a second importer."""

    if is_desktop_mode():
        _monitor_desktop_mt5_exports()
    else:
        _monitor_local_mt5_exports(repo)


def render_auto_sync_notice() -> None:
    notice = st.session_state.pop("auto_sync_notice", None)
    if notice:
        st.success(notice)


def render_sync_failures(results: list[MT5AutoSyncResult], *, prefix: str) -> None:
    failures: list[tuple[str, str]] = []
    for item in results:
        if item.status == "failed":
            failures.append((item.account_name, item.message or "Unknown error"))
        if item.live_status == "failed":
            failures.append((f"{item.account_name} live positions", item.live_message or "Unknown error"))
    if not failures:
        return
    details = "; ".join(f"{account_name}: {message}" for account_name, message in failures)
    st.error(f"{prefix}: {details}")


@st.fragment(run_every=_FRESHNESS_INTERVAL_SECONDS)
def render_live_sync_freshness(account_key: tuple[str, str] | None = None, *, include_sync_hint: bool = False) -> None:
    results = st.session_state.get("auto_sync_results", [])
    if account_key is not None:
        account_login, broker_server = account_key
        result = next(
            (item for item in results if (item.account_login, item.broker_server) == (account_login, broker_server)),
            None,
        )
        last_update = result.export_updated_at if result else None
    else:
        last_update = max((item.export_updated_at for item in results if item.export_updated_at is not None), default=None)
    message = f"Last update: {format_relative_time_localized(format_relative_time(last_update))}" if last_update is not None else "No export checked yet"
    if include_sync_hint:
        message += " · Checks every configured account immediately (read-only)."
    st.caption(message)


def render_manual_sync_button(repo: SQLiteJournalRepository, *, key: str) -> None:
    actions = st.container(horizontal=True, vertical_alignment="center", gap="medium")
    sync_requested = actions.button("Sync MT5 now", key=key, icon=":material/sync:")
    results = st.session_state.get("auto_sync_results", [])
    if sync_requested:
        if is_desktop_mode():
            paths = desktop_runtime_paths()
            DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path).request_sync()
            st.info(tr("Desktop sync requested. The local worker will check every configured export within one second."))
        else:
            with st.spinner(tr("Syncing MT5 now…")):
                results = MT5AutoSyncService(repo).sync_configured_exports()
            st.session_state["auto_sync_results"] = results
            st.toast(tr("MT5 sync complete."), icon=":material/sync:")
    with actions:
        render_live_sync_freshness(include_sync_hint=True)
    if not sync_requested or is_desktop_mode():
        return

    imported = [item for item in results if item.status == "imported"]
    failures = [item for item in results if item.status == "failed" or item.live_status == "failed"]
    waiting = [item for item in results if item.status == "waiting"]
    if imported:
        created = sum(item.created_count for item in imported)
        updated = sum(item.updated_count for item in imported)
        st.session_state["auto_sync_notice"] = f"Manual sync imported {created} created and {updated} updated MT5 position(s)."
        st.rerun()
    elif not failures and waiting:
        st.info(tr("MT5 sync is waiting: ") + "; ".join(item.message or item.account_name for item in waiting))
    elif not failures and results:
        st.success("MT5 journal is already up to date.")
    elif not failures:
        st.info("No approved MT5 accounts are configured for sync.")
    render_sync_failures(results, prefix="MT5 sync needs attention")


def render_journal_reporting_settings(repo: SQLiteJournalRepository) -> None:
    st.markdown("#### Reporting calendar")
    with st.form("journal-reporting-settings", border=False):
        current = repo.get_journal_settings()
        labels = {"server": "Server Timezone", "utc": "UTC", "local": "Local Timezone"}
        selected = st.segmented_control(
            "Group trades and reports by",
            list(labels.values()),
            default=labels[current.reporting_time_basis],
            required=True,
            width="content",
        )
        st.caption("Choose the calendar used for reports and limits. Server Timezone follows the MT5 broker clock.")
        submitted = st.form_submit_button("Save calendar", type="primary", icon=":material/save:")
    if submitted:
        try:
            with st.spinner(tr("Saving…")):
                repo.configure_journal(reporting_time_basis=next(key for key, label in labels.items() if label == selected))
        except ValueError as error:
            st.error(str(error))
        else:
            st.toast(tr("Journal settings saved."), icon=":material/check_circle:")
            st.success("Journal settings saved.")


def _clear_account_onboarding() -> None:
    for key in [key for key in st.session_state if key.startswith("account-onboarding-")]:
        st.session_state.pop(key, None)


def _render_account_onboarding_dialog(repo: SQLiteJournalRepository, strategy_profiles, common_files_location) -> None:  # type: ignore[no-untyped-def]
    """Collect the minimum system, account, capital, and risk evidence before import."""
    prefix = "account-onboarding-"
    step_key = f"{prefix}step"
    step = st.session_state.setdefault(step_key, 1)
    profile_by_id = {profile.id: profile for profile in strategy_profiles}
    defaults = {
        "mode": "Use saved system" if strategy_profiles else "Create system",
        "strategy-id": strategy_profiles[0].id if strategy_profiles else None,
        "strategy-name": "",
        "strategy-rules": "",
        "account-name": "",
        "currency": "USD",
        "funded-capital": "",
        "login": "",
        "broker": "",
        "export-path": "",
        "standard-risk": "2",
        "maximum-risk": "3",
        "daily-loss": "5",
        "weekly-loss": "20",
        "max-drawdown": "30",
        "drawdown-reset-period": "daily",
        "max-open-risk": "1",
        "max-consecutive-losses": 10,
        "loss-streak-reset-period": "daily",
        "minimum-rr": "1.0",
        "correlation-policy": "",
    }

    def value(name: str):  # type: ignore[no-untyped-def]
        return st.session_state.get(f"{prefix}draft-{name}", st.session_state.get(f"{prefix}{name}", defaults[name]))

    def stash(names: tuple[str, ...]) -> None:
        for name in names:
            st.session_state[f"{prefix}draft-{name}"] = value(name)

    def sync(name: str) -> None:
        st.session_state[f"{prefix}draft-{name}"] = st.session_state[f"{prefix}{name}"]

    def previous() -> None:
        st.session_state[step_key] = max(1, step - 1)
        st.rerun()

    def next_step() -> None:
        st.session_state[step_key] = min(3, step + 1)
        st.rerun()

    st.caption("Complete the three essentials before this account can import trades and become active.")
    st.progress(step / 3, text=f"Step {step} of 3")
    step_labels = ("1. System", "2. MT5 account", "3. Risk policy")
    st.caption(" · ".join(f"**{label}**" if index == step else label for index, label in enumerate(step_labels, start=1)))
    with st.container(border=True):
        if step == 1:
            st.markdown("###### Trading system")
            mode_options = ["Create system"] if not strategy_profiles else ["Use saved system", "Create system"]
            mode_default = value("mode") if value("mode") in mode_options else mode_options[0]
            st.segmented_control(tr("System source"), mode_options, format_func=tr, default=mode_default, key=f"{prefix}mode", required=True, on_change=sync, args=("mode",))
            if value("mode") == "Use saved system":
                strategy_ids = list(profile_by_id)
                strategy_index = strategy_ids.index(value("strategy-id")) if value("strategy-id") in strategy_ids else 0
                st.selectbox("Trading system", strategy_ids, index=strategy_index, format_func=lambda item: profile_by_id[item].name, key=f"{prefix}strategy-id", on_change=sync, args=("strategy-id",))
                st.caption("You can change this later, until the account has imported trades.")
            else:
                st.text_input("Strategy name", value=value("strategy-name"), placeholder="e.g. London continuation", key=f"{prefix}strategy-name", on_change=sync, args=("strategy-name",))
                st.text_area("Strategy description", value=value("strategy-rules"), placeholder="When and why this setup should be traded.", key=f"{prefix}strategy-rules", on_change=sync, args=("strategy-rules",))
            if st.button("Continue to MT5 account", type="primary", icon=":material/arrow_forward:"):
                if value("mode") == "Use saved system" and value("strategy-id") not in profile_by_id:
                    st.error("Select a saved trading system.")
                elif value("mode") == "Use saved system":
                    selected_profile = profile_by_id[value("strategy-id")]
                    if not (selected_profile.description or "").strip():
                        st.error("The selected system needs a strategy description. Complete it in Strategies or create a system here.")
                    else:
                        stash(("mode", "strategy-id", "strategy-name", "strategy-rules"))
                        next_step()
                elif value("mode") == "Create system" and not all(str(value(name)).strip() for name in ("strategy-name", "strategy-rules")):
                    st.error("Add a strategy name and description.")
                else:
                    stash(("mode", "strategy-id", "strategy-name", "strategy-rules"))
                    next_step()
        elif step == 2:
            st.markdown("###### MT5 account connection")
            first, second = st.columns(2)
            account_name = first.text_input("Account name", value=value("account-name"), placeholder="e.g. Live account", key=f"{prefix}account-name", on_change=sync, args=("account-name",))
            currency = second.text_input("Currency", value=value("currency"), max_chars=3, key=f"{prefix}currency", on_change=sync, args=("currency",))
            first, second = st.columns(2)
            login = first.text_input("MT5 account ID", value=value("login"), key=f"{prefix}login", on_change=sync, args=("login",))
            broker = second.text_input("Broker server", value=value("broker"), key=f"{prefix}broker", on_change=sync, args=("broker",))
            funded_capital = st.text_input("Funded capital", value=value("funded-capital"), placeholder="e.g. 10000", key=f"{prefix}funded-capital", on_change=sync, args=("funded-capital",))
            st.caption("This required baseline anchors account growth, drawdown, and the risk policy. It does not replace MT5 balance history.")
            with st.expander("Advanced: custom export location"):
                if common_files_location.path is not None:
                    st.caption(f"Detected Common Files ({common_files_location.source}): {common_files_location.path}")
                st.text_input("Custom export path (optional)", value=value("export-path"), placeholder="Default MT5 Common Files path", key=f"{prefix}export-path", on_change=sync, args=("export-path",))
            back, forward = st.columns(2)
            if back.button("Back", icon=":material/arrow_back:"):
                previous()
            if forward.button("Continue to risk policy", type="primary", icon=":material/arrow_forward:"):
                if not account_name.strip() or not login.isdecimal() or not broker.strip():
                    st.error("Enter an account name, numeric MT5 account ID, and broker server.")
                elif len(currency.strip()) != 3:
                    st.error("Enter a three-letter currency code.")
                else:
                    try:
                        if Decimal(funded_capital) <= 0:
                            raise ValueError
                    except Exception:
                        st.error("Enter a positive funded-capital amount.")
                    else:
                        st.session_state[f"{prefix}draft-account-name"] = account_name
                        st.session_state[f"{prefix}draft-currency"] = currency
                        st.session_state[f"{prefix}draft-login"] = login
                        st.session_state[f"{prefix}draft-broker"] = broker
                        st.session_state[f"{prefix}draft-funded-capital"] = funded_capital
                        stash(("export-path",))
                        next_step()
        else:
            st.markdown("###### Risk policy")
            st.caption("Review the conservative defaults and adjust them to your written plan. Every limit is required.")
            first, second, third = st.columns(3)
            first.text_input("Standard risk (1R) %", value=value("standard-risk"), key=f"{prefix}standard-risk")
            second.text_input("Maximum risk per trade %", value=value("maximum-risk"), key=f"{prefix}maximum-risk")
            third.text_input("Daily loss limit (R)", value=value("daily-loss"), key=f"{prefix}daily-loss")
            first, second, third = st.columns(3)
            first.text_input("Weekly loss limit (R)", value=value("weekly-loss"), key=f"{prefix}weekly-loss")
            second.text_input("Maximum drawdown %", value=value("max-drawdown"), key=f"{prefix}max-drawdown")
            third.text_input("Maximum open risk (R)", value=value("max-open-risk"), key=f"{prefix}max-open-risk")
            first, second = st.columns(2)
            first.number_input("Maximum consecutive losses", min_value=1, step=1, value=value("max-consecutive-losses"), key=f"{prefix}max-consecutive-losses")
            second.text_input("Minimum R:R", value=value("minimum-rr"), key=f"{prefix}minimum-rr")
            reset_labels = {tr("Daily"): "daily", tr("Weekly"): "weekly", tr("Monthly"): "monthly", tr("All time"): "all_time"}
            first, second = st.columns(2)
            drawdown_reset = first.segmented_control(
                tr("Drawdown reset"),
                list(reset_labels),
                default=next(label for label, stored in reset_labels.items() if stored == value("drawdown-reset-period")),
                key=f"{prefix}drawdown-reset-period-label",
                required=True,
            )
            streak_reset = second.segmented_control(
                tr("Losing-streak reset"),
                list(reset_labels),
                default=next(label for label, stored in reset_labels.items() if stored == value("loss-streak-reset-period")),
                key=f"{prefix}loss-streak-reset-period-label",
                required=True,
            )
            st.text_input("Correlation policy (optional)", value=value("correlation-policy"), key=f"{prefix}correlation-policy")
            st.markdown("###### Confirm account setup")
            system_label = profile_by_id[value("strategy-id")].name if value("mode") == "Use saved system" else value("strategy-name")
            st.caption(f"**System:** {system_label} · **MT5:** {value('login')} · {value('broker')} · **Capital:** {value('funded-capital')} {str(value('currency')).upper()}")
            back, create = st.columns(2)
            if back.button("Back", icon=":material/arrow_back:"):
                previous()
            if create.button("Create and activate account", type="primary", icon=":material/check_circle:"):
                try:
                    resolved_export_path = str(value("export-path")).strip() or default_mt5_export_path(str(value("login")))
                    account = repo.create_configured_mt5_account(
                        display_name=str(value("account-name")), login=str(value("login")), broker_server=str(value("broker")),
                        account_currency=str(value("currency")), export_file_path=resolved_export_path, funded_capital=str(value("funded-capital")),
                        strategy_profile_id=value("strategy-id") if value("mode") == "Use saved system" else None,
                        strategy_name=str(value("strategy-name")), strategy_description=str(value("strategy-rules")),
                        standard_risk_per_trade_percent=str(value("standard-risk")), maximum_risk_per_trade_percent=str(value("maximum-risk")),
                        daily_loss_limit_r=str(value("daily-loss")), weekly_loss_limit_r=str(value("weekly-loss")),
                        max_drawdown_percent=str(value("max-drawdown")), max_open_risk_r=str(value("max-open-risk")),
                        max_consecutive_losses=int(value("max-consecutive-losses")), minimum_rr=str(value("minimum-rr")),
                        correlation_policy=str(value("correlation-policy")) or None,
                        drawdown_reset_period=reset_labels[drawdown_reset],
                        loss_streak_reset_period=reset_labels[streak_reset],
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state["mt5-account-selected-id"] = str(account.id)
                    notice = f"{account.display_name} is configured and active."
                    _clear_account_onboarding()
                    st.session_state["mt5-account-notice"] = notice
                    queue_toast(tr(notice))
                    st.rerun()


def render_mt5_account_settings(repo: SQLiteJournalRepository) -> AccountListItem | None:
    st.markdown("#### Approved MT5 accounts")
    st.caption("Each account has one trading system. Its broker server confirms the export source. Funded capital can be updated later; it recalculates historical growth, drawdown, and Risk limits without changing MT5 trades. Dashboard and Framework always show the single active account below.")
    common_files_location = find_mt5_common_files()
    accounts = repo.list_mt5_accounts()
    disabled_accounts = repo.list_disabled_mt5_accounts()
    active_account = repo.get_active_mt5_account()
    if active_account is not None:
        accounts.sort(key=lambda account: account.id != active_account.id)
    strategy_profiles = [profile for profile in repo.list_strategy_profiles() if profile.name != "Journal default"]
    profiles_by_id = {profile.id: profile for profile in strategy_profiles}
    accounts_by_id = {str(account.id): account for account in accounts}
    selected_id = st.session_state.get("mt5-account-selected-id")
    if selected_id not in accounts_by_id and selected_id != "new":
        selected_id = str(accounts[0].id) if accounts else "new"
        st.session_state["mt5-account-selected-id"] = selected_id

    def begin_new_account() -> None:
        _clear_account_onboarding()
        st.session_state["mt5-account-selected-id"] = "new"
        st.session_state["account-onboarding-open"] = True

    def activate_account(account_id: int, display_name: str) -> None:
        repo.set_active_mt5_account(account_id)
        st.session_state["mt5-account-notice"] = tr("{account} is now the active account.", account=display_name)
        queue_toast(st.session_state["mt5-account-notice"], icon=":material/toggle_on:")

    def reactivate_account(account_id: int, display_name: str) -> None:
        repo.reactivate_mt5_account(account_id)
        st.session_state["mt5-account-selected-id"] = str(account_id)
        st.session_state["mt5-account-notice"] = tr(
            "{account} was reactivated and is available for imports and reports.", account=display_name
        )
        queue_toast(st.session_state["mt5-account-notice"], icon=":material/restore:")

    master, detail = st.columns([2, 3], gap="large")
    with master:
        st.markdown("##### Accounts")
        st.button("New account", icon=":material/add:", width="stretch", key="new-mt5-account", on_click=begin_new_account)
        if accounts:
            for account in accounts:
                is_active = active_account is not None and account.id == active_account.id
                is_editing = selected_id == str(account.id)
                with st.container(border=True):
                    st.markdown(f"**{account.display_name}**")
                    if is_active:
                        st.caption(tr("Active"))
                    st.caption(f"{account.strategy_name} · {account.login} · {account.broker_server}")
                    if account.funded_capital:
                        st.caption(f"{tr('Funded capital')}: {format_currency_caption(account.funded_capital, account.account_currency, signed=False)}")
                    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                        if not is_active:
                            st.button(
                                tr("Set active"),
                                type="primary",
                                icon=":material/toggle_on:",
                                key=f"quick-activate-mt5-account-{account.id}",
                                width="stretch",
                                on_click=activate_account,
                                args=(account.id, account.display_name),
                            )
                        if is_editing:
                            st.badge(tr("Currently editing"), icon=":material/edit_note:")
                        elif st.button("Edit", icon=":material/edit:", key=f"open-mt5-account-{account.id}", width="stretch"):
                            st.session_state["mt5-account-selected-id"] = str(account.id)
                            st.rerun()
        else:
            st.caption("No accounts yet. Create the first one to start importing MT5 trades.")
        if disabled_accounts:
            with st.expander(f"{tr('Disabled accounts')} ({len(disabled_accounts)})"):
                st.caption(
                    tr(
                        "Disabled accounts retain their imported trades, reviews, trading system, and risk policy."
                    )
                )
                for account in disabled_accounts:
                    with st.container(border=True):
                        st.markdown(f"**{account.display_name}**")
                        st.caption(f"{account.strategy_name} · {account.login} · {account.broker_server}")
                        st.button(
                            tr("Reactivate"),
                            icon=":material/restore:",
                            key=f"reactivate-mt5-account-{account.id}",
                            width="stretch",
                            on_click=reactivate_account,
                            args=(account.id, account.display_name),
                        )

    if st.session_state.get("account-onboarding-open"):
        st.dialog(tr("Create import-ready account"), width="large", on_dismiss=_clear_account_onboarding)(_render_account_onboarding_dialog)(
            repo, strategy_profiles, common_files_location
        )

    selected = accounts_by_id.get(selected_id)
    if selected is None:
        with detail:
            notice = st.session_state.pop("mt5-account-notice", None)
            if notice:
                st.success(notice)
            st.info("No account selected. Click **+ New account** above to create one.")
        return None
    form_scope = str(selected.id)

    def field_key(name: str) -> str:
        return f"mt5-account-{form_scope}-{name}"

    defaults = {
        "display-name": selected.display_name,
        "currency": selected.account_currency,
        "funded-capital": selected.funded_capital or "",
        "login": selected.login,
        "broker-server": selected.broker_server,
        "export-path": "" if selected.export_file_path == default_mt5_export_path(selected.login) else selected.export_file_path,
        "strategy": selected.strategy_profile_id,
    }
    for name, value in defaults.items():
        st.session_state.setdefault(field_key(name), value)

    has_imported_trades = repo.account_has_imported_trades(selected.id)
    identity_locked = has_imported_trades

    def clear_account_form() -> None:
        for name in defaults:
            st.session_state.pop(field_key(name), None)

    def save_account() -> bool:
        display_name = st.session_state[field_key("display-name")].strip()
        account_currency = st.session_state[field_key("currency")].upper()
        funded_capital = st.session_state[field_key("funded-capital")]
        login = st.session_state[field_key("login")]
        broker_server = st.session_state[field_key("broker-server")].strip()
        export_file_path = st.session_state[field_key("export-path")]
        strategy_profile_id = st.session_state[field_key("strategy")]
        if not display_name or not login.isdecimal() or not broker_server:
            st.session_state["mt5-account-save-error"] = "An account name, a numeric MT5 login, and broker server are required."
            return False
        try:
            if Decimal(funded_capital) <= 0:
                raise ValueError
        except Exception:
            st.session_state["mt5-account-save-error"] = "Enter a positive funded-capital amount."
            return False

        resolved_export_path = export_file_path.strip() or default_mt5_export_path(login)
        try:
            with st.spinner(tr("Saving…")):
                repo.update_mt5_account(
                    account_id=selected.id,
                    display_name=display_name,
                    login=login,
                    broker_server=broker_server,
                    account_currency=account_currency,
                    export_file_path=resolved_export_path,
                    opening_balance=funded_capital or None,
                    strategy_profile_id=None if identity_locked else strategy_profile_id,
                )
        except ValueError as error:
            st.session_state["mt5-account-save-error"] = str(error)
            return False
        st.session_state["mt5-account-notice"] = "MT5 account updated."
        queue_toast(tr(st.session_state["mt5-account-notice"]))
        return True

    with detail:
        account_editor = st.container(border=True)
        header_name, header_status = account_editor.columns([3, 2], vertical_alignment="center")
        header_name.markdown(f"##### {selected.display_name}")
        if active_account is not None and selected.id == active_account.id:
            header_status.badge(tr("Active"), color="green", icon=":material/check_circle:")
        else:
            activate_clicked = header_status.button(
                "Set as active account",
                icon=":material/toggle_on:",
                key=f"set-active-mt5-account-{selected.id}",
            )
            if activate_clicked:
                with st.spinner(tr("Saving…")):
                    repo.set_active_mt5_account(selected.id)
                st.session_state["mt5-account-notice"] = tr("{account} is now the active account.", account=selected.display_name)
                queue_toast(st.session_state["mt5-account-notice"], icon=":material/toggle_on:")
                st.rerun()
        if identity_locked:
            account_editor.caption("MT5 account ID, broker server, and currency are locked because this account already has imported trades. Its name, funded capital, and export location remain editable.")
        with account_editor.form("mt5-account-editor-form", border=False):
            identity, currency, baseline = st.columns([3, 1, 2])
            display_name = identity.text_input(
                "Account name",
                placeholder="e.g. Live account, Prop firm eval",
                key=field_key("display-name"),
            )
            account_currency = currency.text_input("Currency", max_chars=3, key=field_key("currency"), disabled=identity_locked).upper()
            funded_capital = baseline.text_input(
                "Funded capital",
                placeholder="e.g. 10000",
                help="The fixed basis for balance growth, drawdown, and Risk limits; it does not replace the latest live MT5 balance.",
                key=field_key("funded-capital"),
            )
            account_id, broker = st.columns(2)
            login = account_id.text_input("MT5 account ID", key=field_key("login"), disabled=identity_locked)
            broker_server = broker.text_input("Broker server", key=field_key("broker-server"), disabled=identity_locked)
            if identity_locked:
                account_editor.caption(f"Trading system: **{selected.strategy_name}** · locked because trades have been imported.")
            else:
                strategy_options = dict(profiles_by_id)
                if selected.strategy_profile_id not in strategy_options:
                    strategy_options[selected.strategy_profile_id] = repo.get_account_strategy(selected.id)
                st.selectbox(
                    "Trading system",
                    list(strategy_options),
                    format_func=lambda item: strategy_options[item].name,
                    key=field_key("strategy"),
                    help="You can change this until the account has imported trades.",
                )
            with st.expander("Advanced: custom export location"):
                if common_files_location.path is None:
                    st.caption("MT5 Common Files was not detected. Set a custom path or `TRADING_JOURNAL_MT5_COMMON_FILES`.")
                else:
                    st.caption(f"Detected Common Files ({common_files_location.source})")
                    st.caption(str(common_files_location.path))
                generated_path_placeholder = default_mt5_export_path("000000000").replace("000000000", "<MT5-login>")
                export_file_path = st.text_input(
                    "Custom export path (optional)",
                    placeholder=generated_path_placeholder,
                    help="Leave blank to use the EA default: trading_journal/<MT5-login>_positions.csv.",
                    key=field_key("export-path"),
                )
                st.caption("Default: `trading_journal/<MT5-login>_positions.csv` under MT5 Common Files.")
            account_submitted = st.form_submit_button(
                "Update account",
                type="primary",
                icon=":material/save:",
            )
        if account_submitted and save_account():
            st.rerun()

        save_error = st.session_state.pop("mt5-account-save-error", None)
        if save_error:
            st.error(save_error)

        with st.expander("Account maintenance"):
            if has_imported_trades:
                st.caption("This account has imported trades or reviews. Disable it to remove it from imports and reports while retaining its local history. Reactivate it later from Disabled accounts.")

                disable_clicked = st.button("Disable account", key=f"disable-mt5-account-{selected.id}")
                if disable_clicked:
                    with st.spinner(tr("Disabling account…")):
                        repo.deactivate_mt5_account(selected.id)
                    clear_account_form()
                    st.session_state["mt5-account-selected-id"] = "new"
                    st.session_state["mt5-account-notice"] = "MT5 account disabled."
                    queue_toast(tr("MT5 account disabled."), icon=":material/toggle_off:")
                    st.rerun()
            else:
                st.caption("This account has no imported trades. Deleting it permanently removes its account settings, import log, and risk-policy setup.")
                delete_confirmation_key = f"delete-mt5-account-confirm-{selected.id}"
                delete_confirmed = st.checkbox(
                    "I understand this permanently deletes this unused account",
                    key=delete_confirmation_key,
                )

                delete_clicked = st.button(
                    "Delete account",
                    type="primary",
                    key=f"delete-mt5-account-{selected.id}",
                    disabled=not delete_confirmed,
                )
                if delete_clicked:
                    if not st.session_state.get(delete_confirmation_key, False):
                        st.session_state["mt5-account-save-error"] = "Confirm deletion before deleting the account."
                    else:
                        with st.spinner(tr("Deleting account…")):
                            repo.delete_mt5_account(selected.id)
                        clear_account_form()
                        st.session_state["mt5-account-selected-id"] = "new"
                        st.session_state["mt5-account-notice"] = "MT5 account deleted."
                        queue_toast(tr("MT5 account deleted."), icon=":material/delete:")
                        st.rerun()

        notice = st.session_state.pop("mt5-account-notice", None)
        if notice:
            st.success(notice)
    return selected

def render_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Settings")
    st.caption("Configure reporting, account risk, reusable strategies, and review rules.")
    accounts_tab, strategies_tab, context_tab, rules_tab = st.tabs(["Account & risk", "Strategies", "Review context", "Review rules"])
    with accounts_tab:
        account = render_mt5_account_settings(repo)
        st.divider()
        if account is None:
            st.info(tr("Save an MT5 account before configuring its Risk policy."))
        else:
            _render_risk_policy(repo, account)
    with strategies_tab:
        render_strategy_settings(repo)
    with context_tab:
        render_journal_reporting_settings(repo)
        st.divider()
        render_review_context_settings(repo)
    with rules_tab:
        _render_framework_rules(repo)
    if is_desktop_mode():
        st.divider()
        with st.container(border=True):
            st.markdown("##### Desktop application")
            st.caption("The journal, MT5 sync worker, and your data are running locally on this computer. Closing this desktop application stops automatic MT5 imports.")
            if st.button("Quit desktop journal", icon=":material/power_settings_new:", type="primary"):
                paths = desktop_runtime_paths()
                DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path, paths.reset_request_path).request_shutdown()
                st.success(tr("Closing the local Trade Compass…"))
        render_desktop_database_reset()


def render_desktop_database_reset() -> None:
    """Request a supervisor-owned reset; the browser never deletes SQLite files."""

    pending_reset_id = st.session_state.get("desktop-database-reset-pending")
    if pending_reset_id:
        st.info("Restarting Trade Compass. This page will reload automatically when the clean journal is ready.")
        ready_reset_id = render_desktop_reset_restart_bridge(pending_reset_id)
        if ready_reset_id == pending_reset_id and st.session_state.get("desktop-database-reset-dispatched") != pending_reset_id:
            request_desktop_database_reset()
            st.session_state["desktop-database-reset-dispatched"] = pending_reset_id
        return

    with st.container(border=True):
        st.markdown("##### Reset local database")
        st.warning("This permanently removes all local accounts, imports, reviews, policies, strategies, and framework evidence. MT5 export files and desktop logs are kept.")
        confirmation = st.text_input("Type RESET to confirm", key="desktop-database-reset-confirmation")
        if st.button(
            "Reset local database",
            icon=":material/delete_forever:",
            type="primary",
            disabled=confirmation.strip() != "RESET",
            key="desktop-database-reset",
        ):
            st.session_state["desktop-database-reset-pending"] = uuid4().hex
            st.session_state.pop("desktop-database-reset-dispatched", None)
            st.rerun()


def request_desktop_database_reset() -> None:
    """Send the reset signal only after the browser restart bridge is ready."""

    paths = desktop_runtime_paths()
    DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path, paths.reset_request_path).request_reset()


def render_desktop_database_diagnostic(error: Exception) -> None:
    """Keep unexpected local database failures in the browser, without resetting data."""

    st.set_page_config(page_title="Trade Compass recovery", page_icon=TRADE_COMPASS_ICON, layout="wide")
    st.title("Trade Compass recovery")
    st.error("Trade Compass could not open its local database.")
    st.caption("No data was changed. Inspect desktop.log in the Trade Compass data directory before taking further action.")
    st.code(str(error), language="text")
    print("Trade Compass diagnostic recovery screen active.", flush=True)


def render_review_context_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Review context")
    st.caption("Maintain short reusable lists so setup, session, and regime reports stay comparable. Context is optional on Deep Reviews.")
    for kind, label, placeholder in (
        ("session", "Sessions", "e.g. London"),
        ("regime", "Market regimes", "e.g. Trending"),
    ):
        with st.container(border=True):
            st.markdown(f"##### {label}")
            existing = repo.list_review_context_tags(kind, include_inactive=True)
            if existing:
                st.dataframe(
                    pd.DataFrame([{"Name": item.name, "Active": item.active} for item in existing]),
                    hide_index=True,
                    width="stretch",
                )
            with st.form(f"review-context-{kind}", border=False):
                name = st.text_input(f"Add {kind}", placeholder=placeholder)
                if st.form_submit_button(f"Add {kind}", icon=":material/add:"):
                    try:
                        repo.save_review_context_tag(kind=kind, name=name)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        queue_toast(f"{label[:-1]} added.")
                        st.rerun()


def render_strategy_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Strategy library")
    st.caption("Save reusable strategy definitions and their backtest evidence. Each MT5 account selects one system when it is created; a system may be shared by more than one account.")
    profiles = [profile for profile in repo.list_strategy_profiles() if profile.name != "Journal default"]

    profiles_by_id = {profile.id: profile for profile in profiles}
    bound_account_counts: dict[int, int] = {}
    for account in repo.list_mt5_accounts():
        bound_account_counts[account.strategy_profile_id] = bound_account_counts.get(account.strategy_profile_id, 0) + 1
    selected_id = st.session_state.get("strategy-selected-id")
    if selected_id not in profiles_by_id and selected_id != "new":
        selected_id = profiles[0].id if profiles else "new"
        st.session_state["strategy-selected-id"] = selected_id

    def begin_new_strategy() -> None:
        st.session_state["strategy-selected-id"] = "new"
        for name in (
            "name",
            "description",
            "backtest-verified",
            "backtest-notes",
        ):
            st.session_state.pop(f"strategy-new-{name}", None)

    master, detail = st.columns([1, 2], gap="large")
    with master:
        st.markdown("##### Strategies")
        st.button("Add strategy", icon=":material/add:", width="stretch", key="new-strategy", on_click=begin_new_strategy)
        if profiles:
            for profile in profiles:
                is_editing = selected_id == profile.id
                with st.container(border=True):
                    st.markdown(f"**{profile.name}**")
                    if profile.backtest_verified:
                        st.caption("✅ " + tr("Backtest verified"))
                    if profile.description:
                        preview = profile.description.strip()
                        if len(preview) > 80:
                            preview = preview[:80].rstrip() + "…"
                        st.caption(preview)
                    setup_count = len(repo.list_strategy_setups(profile.id, include_inactive=True))
                    bound_count = bound_account_counts.get(profile.id, 0)
                    counts = []
                    if bound_count:
                        counts.append(tr("{count} account", count=bound_count) if bound_count == 1 else tr("{count} accounts", count=bound_count))
                    if setup_count:
                        counts.append(tr("{count} setup", count=setup_count) if setup_count == 1 else tr("{count} setups", count=setup_count))
                    if counts:
                        st.caption(" · ".join(counts))
                    if is_editing:
                        st.badge(tr("Currently editing"), icon=":material/edit_note:")
                    elif st.button("Edit", icon=":material/edit:", key=f"open-strategy-{profile.id}", width="stretch"):
                        st.session_state["strategy-selected-id"] = profile.id
                        st.rerun()
        else:
            st.caption("No strategies yet. Create one before reviewing trade quality.")

    selected = profiles_by_id.get(selected_id)
    form_scope = "new" if selected is None else str(selected.id)

    def field_key(name: str) -> str:
        return f"strategy-{form_scope}-{name}"

    defaults = {
        "name": selected.name if selected else "",
        "description": selected.description or "" if selected else "",
        "backtest-verified": bool(selected.backtest_verified) if selected else False,
        "backtest-notes": selected.backtest_notes or "" if selected else "",
    }
    for name, value in defaults.items():
        st.session_state.setdefault(field_key(name), value)
    has_backtest = bool(selected and (selected.backtest_verified or selected.backtest_notes))

    with detail:
        strategy_editor = st.container(border=True)
        strategy_editor.markdown(f"##### {'New strategy' if selected is None else selected.name}")
        with strategy_editor.form("strategy-profile", border=False):
            name = st.text_input("Strategy name", key=field_key("name"))
            description = st.text_area(
                "Strategy description",
                placeholder="When and why this setup should be traded.",
                key=field_key("description"),
            )
            with st.expander("Add backtest evidence (optional)", expanded=has_backtest):
                st.caption("Leave this closed unless you want to record the evidence behind this strategy.")
                backtest_verified = st.checkbox(
                    "Backtest verified",
                    key=field_key("backtest-verified"),
                    help="Confirms you have reviewed a trustworthy backtest showing this strategy has a positive edge.",
                )
                backtest_notes = st.text_area("Backtest notes", placeholder="Market, timeframe, rules, and any material caveats.", key=field_key("backtest-notes"))
                st.caption("Backtest evidence applies to reviews you save from now on. Trades you've already reviewed keep the evidence saved with them, so their Trading system score doesn't change.")
            submitted = st.form_submit_button("Save strategy", type="primary", icon=":material/save:")
        if submitted:
            try:
                with st.spinner(tr("Saving…")):
                    profile = repo.save_strategy_profile(
                        name=name,
                        description=description or None,
                        backtest_verified=backtest_verified,
                        backtest_notes=backtest_notes or None,
                        strategy_id=selected.id if selected else None,
                    )
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["strategy-selected-id"] = profile.id
                queue_toast(tr("Strategy profile saved."))
                st.rerun()

        if selected is not None:
            with st.expander("Strategy setups", expanded=False):
                st.caption("Optional controlled setup names for Deep Review and System reports.")
                setups = repo.list_strategy_setups(selected.id, include_inactive=True)
                setups_by_id = {setup.id: setup for setup in setups}
                setup_selected_key = f"strategy-setup-selected-id-{selected.id}"
                setup_selected_id = st.session_state.get(setup_selected_key, "new")
                if setup_selected_id not in setups_by_id and setup_selected_id != "new":
                    setup_selected_id = "new"
                    st.session_state[setup_selected_key] = setup_selected_id

                def begin_new_setup() -> None:
                    st.session_state[setup_selected_key] = "new"

                if setups:
                    for setup in setups:
                        is_editing = setup_selected_id == setup.id
                        with st.container(border=True):
                            st.markdown(f"**{setup.name}**")
                            st.caption(tr("Active") if setup.active else tr("Inactive"))
                            if setup.description:
                                st.caption(setup.description)
                            if is_editing:
                                st.badge(tr("Currently editing"), icon=":material/edit_note:")
                            elif st.button(
                                "Edit", icon=":material/edit:", key=f"open-strategy-setup-{setup.id}", width="stretch"
                            ):
                                st.session_state[setup_selected_key] = setup.id
                                st.rerun()
                    st.button("+ New setup", key=f"new-strategy-setup-{selected.id}", on_click=begin_new_setup)

                setup_selected = setups_by_id.get(setup_selected_id)
                new_setup_generation_key = f"strategy-setup-new-generation-{selected.id}"
                new_setup_generation = st.session_state.get(new_setup_generation_key, 0)

                def setup_field_key(name: str) -> str:
                    scope = setup_selected_id if setup_selected_id != "new" else f"new-{new_setup_generation}"
                    return f"strategy-setup-{selected.id}-{scope}-{name}"

                with st.form(f"strategy-setup-{selected.id}-{setup_selected_id}", border=False):
                    setup_name = st.text_input(
                        "Setup name", value=setup_selected.name if setup_selected else "", placeholder="e.g. London pullback", key=setup_field_key("name")
                    )
                    setup_description = st.text_input(
                        "Setup description (optional)",
                        value=(setup_selected.description or "") if setup_selected else "",
                        key=setup_field_key("description"),
                    )
                    setup_active = (
                        st.checkbox("Active", value=setup_selected.active, key=setup_field_key("active")) if setup_selected is not None else True
                    )
                    submit_label = "Update setup" if setup_selected is not None else "Add setup"
                    if st.form_submit_button(submit_label, icon=":material/save:" if setup_selected is not None else ":material/add:"):
                        try:
                            repo.save_strategy_setup(
                                strategy_profile_id=selected.id,
                                name=setup_name,
                                description=setup_description or None,
                                setup_id=setup_selected.id if setup_selected is not None else None,
                                active=setup_active,
                            )
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            if setup_selected is None:
                                st.session_state[new_setup_generation_key] = new_setup_generation + 1
                            queue_toast(tr("Strategy setup saved."))
                            st.rerun()

        if selected is not None:
            with st.expander("Strategy maintenance"):
                bound_accounts = [account for account in repo.list_mt5_accounts() if account.strategy_profile_id == selected.id]
                if bound_accounts:
                    account_names = ", ".join(account.display_name for account in bound_accounts)
                    st.caption(f"Bound to {len(bound_accounts)} account(s) ({account_names}). Unbind or delete those accounts first to delete this strategy.")
                else:
                    st.caption("This strategy has no accounts bound to it. Deleting it permanently removes its definition and any setups.")
                    delete_confirmation_key = f"delete-strategy-confirm-{selected.id}"
                    delete_confirmed = st.checkbox(
                        "I understand this permanently deletes this unused strategy", key=delete_confirmation_key
                    )
                    if st.button("Delete strategy", type="primary", key=f"delete-strategy-{selected.id}", disabled=not delete_confirmed):
                        try:
                            repo.delete_strategy_profile(selected.id)
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.session_state["strategy-selected-id"] = "new"
                            queue_toast(tr("Strategy deleted."), icon=":material/delete:")
                            st.rerun()


def render_dashboard(repo: SQLiteJournalRepository) -> AccountListItem | None:
    def render_title() -> None:
        st.markdown('<div class="dashboard-kicker">CLOSED-TRADE REVIEW</div>', unsafe_allow_html=True)
        st.subheader("Performance dashboard")

    try:
        settings = repo.get_journal_settings()
    except RuntimeError:
        render_title()
        st.info(tr("Configure journal settings before viewing reports."))
        return

    account = repo.get_active_mt5_account()
    if account is None:
        render_title()
        st.info(tr("Add an approved MT5 account in Settings before viewing reports."))
        st.page_link("app_pages/settings.py", label=tr("Go to Settings"), icon=":material/settings:")
        return

    render_dashboard_coaching_focus(repo, account)
    dashboard_title, dashboard_sync = st.columns([3, 2], vertical_alignment="center")
    with dashboard_title:
        render_title()
    with dashboard_sync:
        render_manual_sync_button(repo, key="dashboard-manual-sync")
    currency = account.account_currency
    time_label = {"server": "MT5 server time", "utc": "UTC", "local": "local computer time"}[settings.reporting_time_basis]
    st.caption(
        tr("Reporting on {account}. Change the active account in Settings → Approved MT5 accounts.", account=format_account_label(account))
        + f" · {currency} · {time_label}"
    )

    report = build_dashboard_report(repo, account_id=account.id)
    if report.trade_count == 0:
        st.info(tr("No logical trades have been closed yet."))
        return account

    logical_label = f"{report.trade_count} closed logical trade{'s' if report.trade_count != 1 else ''}"
    st.markdown(f'<div class="dashboard-period">{tr("All time")} · {logical_label}</div>', unsafe_allow_html=True)
    chart_view = st.session_state.get("dashboard_chart_view", "Daily")
    if chart_view not in {"Daily", "Per trade"}:
        chart_view = "Daily"
    if chart_view == "Daily":
        displayed_max_drawdown = report.end_of_day_max_drawdown
        displayed_current_drawdown = report.end_of_day_current_drawdown
        displayed_current_drawdown_percent = report.end_of_day_current_drawdown_percent
    else:
        displayed_max_drawdown = report.max_drawdown
        displayed_current_drawdown = report.current_drawdown
        displayed_current_drawdown_percent = report.current_drawdown_percent
    with st.container(border=True):
        _render_stat_grid([
            (
                "Account balance",
                "—" if report.ending_balance is None else format_currency(report.ending_balance, currency, signed=False),
                "info",
            ),
            (
                "Account growth",
                "—" if report.balance_growth_percent is None else format_percent(report.balance_growth_percent, signed=True),
                _signed_metric_tone(report.balance_growth_percent),
            ),
            ("Realized P&L", format_currency(report.net_pnl, currency), _signed_metric_tone(report.net_pnl)),
            (
                "Account drawdown",
                format_currency(-Decimal(displayed_max_drawdown), currency),
                _signed_metric_tone(-Decimal(displayed_max_drawdown)),
            ),
            (
                "Total R",
                "Awaiting risk" if report.total_r is None else format_r(report.total_r),
                _risk_metric_tone(report.total_r, report.trade_count),
            ),
            (
                "Profit factor",
                "No losses" if report.profit_factor is None else format_number(report.profit_factor, 2),
                _profit_factor_metric_tone(report.profit_factor),
            ),
        ])
        if report.r_trade_count < report.trade_count:
            st.caption(f"R is based on {report.r_trade_count:,} of {report.trade_count:,} logical trades with an effective planned risk.")
        else:
            st.caption(f"All {report.trade_count:,} logical trades have an effective risk value.")
        if report.starting_balance is None:
            st.caption("Set funded capital in Settings to enable the balance curve, balance growth, and drawdown percentage.")
        else:
            st.caption(
                f"{tr('Drawdown follows the selected chart view.')} "
                + f"{tr('Current drawdown')}: {format_currency_caption(-Decimal(displayed_current_drawdown), currency)}"
                + (f" ({format_percent(displayed_current_drawdown_percent)})" if displayed_current_drawdown_percent is not None else "")
                + f" · Funded capital: {format_currency_caption(report.starting_balance, currency, signed=False)}."
            )

        _render_dashboard_statistics(report, currency)

    history_surface = st.container(border=True)
    with history_surface:
        history_title, history_control = st.columns([4, 1], vertical_alignment="center")
        with history_title:
            st.markdown(f"#### {tr('Performance history')}")
        with history_control:
            chart_view = st.segmented_control(
                tr("Chart view"),
                ["Daily", "Per trade"],
                format_func=tr,
                default="Daily",
                required=True,
                key="dashboard_chart_view",
                width="stretch",
                label_visibility="collapsed",
            )
    cumulative = pd.DataFrame([item.__dict__ for item in report.cumulative])
    per_trade = pd.DataFrame(
        [item.__dict__ for item in report.per_trade],
        columns=[
            "sequence", "logical_trade_id", "display_label", "position_ids", "position_count", "exit_time", "position_id",
            "symbol", "direction", "net_pnl", "result_r", "strategy", "cumulative_pnl", "balance", "drawdown", "drawdown_percent",
        ],
    )
    daily = pd.DataFrame([item.__dict__ for item in report.daily])
    cumulative["cumulative_pnl"] = cumulative["cumulative_pnl"].astype(float)
    cumulative["balance"] = pd.to_numeric(cumulative["balance"], errors="coerce")
    cumulative["drawdown"] = cumulative["drawdown"].astype(float)
    per_trade["cumulative_pnl"] = per_trade["cumulative_pnl"].astype(float)
    per_trade["drawdown"] = per_trade["drawdown"].astype(float)
    per_trade["net_pnl"] = per_trade["net_pnl"].astype(float)
    per_trade["trade_label"] = per_trade.apply(
        lambda item: f"Trade {item.sequence} · {item.display_label}",
        axis=1,
    )
    per_trade["hover_label"] = per_trade.apply(
        lambda item: f"{item.trade_label}<br>Closed {item.exit_time}",
        axis=1,
    )
    daily["net_pnl"] = daily["net_pnl"].astype(float)

    if chart_view == "Per trade" and not report.per_trade:
        st.info(tr("No logical trades have been closed yet."))
        chart_view = "Daily"

    if chart_view == "Daily":
        timeline = cumulative.assign(hover_label=cumulative["date"])
        timeline_x = timeline["date"]
        curve_column = "balance" if report.ending_balance is not None else "cumulative_pnl"
        curve_title = tr("Account balance curve") if report.ending_balance is not None else tr("Account equity curve · P&L")
        drawdown_title = tr("End-of-day logical-trade drawdown")
        pnl_data = daily.assign(hover_label=daily["date"])
        pnl_x = pnl_data["date"]
        pnl_title = tr("Daily realized P&L")
    else:
        timeline = per_trade
        timeline_x = timeline["exit_time"]
        curve_column = "cumulative_pnl"
        curve_title = tr("Cumulative logical-trade P&L")
        drawdown_title = tr("Logical-trade drawdown")
        pnl_data = per_trade
        pnl_x = pnl_data["exit_time"]
        pnl_title = tr("Logical-trade P&L")
    curve_is_balance = chart_view == "Daily" and report.ending_balance is not None
    timeline["hover_amount"] = [format_currency(value, currency, signed=not curve_is_balance) for value in timeline[curve_column]]
    pnl_data["hover_amount"] = [format_currency(value, currency) for value in pnl_data["net_pnl"]]

    history_figure = _build_performance_history_figure(
        timeline_x=timeline_x,
        curve_values=timeline[curve_column],
        curve_customdata=timeline[["hover_label", "hover_amount"]],
        drawdown_values=-timeline["drawdown"],
        drawdown_customdata=pd.DataFrame(
            {
                "label": timeline["hover_label"],
                "amount": [format_currency(-Decimal(value), currency) for value in timeline["drawdown"]],
            }
        ),
        pnl_x=pnl_x,
        pnl_values=pnl_data["net_pnl"],
        pnl_customdata=pnl_data[["hover_label", "hover_amount"]],
        curve_title=curve_title,
        drawdown_title=drawdown_title,
        pnl_title=pnl_title,
        currency=currency,
        curve_is_balance=curve_is_balance,
    )
    with history_surface:
        st.plotly_chart(
            history_figure,
            width="stretch",
            config={"displayModeBar": False},
            key="dashboard-performance-history",
        )

    with st.container(border=True):
        st.markdown(f"#### {tr('Concentration')}")
        st.caption(tr("Outcome-only views help choose a review sample; they do not prove cause, system quality, or readiness."))
        concentration_options = {tr("Trade"): "trade", tr("Symbol"): "symbol"}
        concentration_choice = st.segmented_control(
            tr("Concentration view"),
            list(concentration_options),
            default=tr("Trade"),
            required=True,
            key="dashboard-concentration-dimension",
            width="content",
        )
        selected_concentration = {
            item.dimension: item for item in report.concentration
        }[concentration_options[concentration_choice]]
        st.plotly_chart(
            _build_concentration_figure(
                profit=selected_concentration.profit,
                loss=selected_concentration.loss,
                currency=currency,
            ),
            width="stretch",
            config={"displayModeBar": False},
            key="dashboard-concentration-pareto",
        )
        profit_summary_column, loss_summary_column = st.columns(2, gap="medium")
        with profit_summary_column:
            st.caption(_concentration_summary(selected_concentration.profit, positive=True))
        with loss_summary_column:
            st.caption(_concentration_summary(selected_concentration.loss, positive=False))

        if chart_view == "Per trade":
            trade_table = pd.DataFrame(
                {
                    tr("Closed"): per_trade["exit_time"],
                    tr("Logical trade"): [f"LT-{trade_id}" for trade_id in per_trade["logical_trade_id"]],
                    tr("Trade"): per_trade["display_label"],
                    tr("Positions"): [
                        ", ".join(f"#{position_id}" for position_id in position_ids) for position_ids in per_trade["position_ids"]
                    ],
                    tr("Symbol"): per_trade["symbol"],
                    tr("Direction"): [tr(direction_tag(value).label) for value in per_trade["direction"]],
                    tr("Outcome"): [tr(outcome_tag(value).label) for value in per_trade["net_pnl"]],
                    f"P&L ({currency})": [format_currency(value, currency) for value in per_trade["net_pnl"]],
                    tr("Result R"): ["—" if value is None else format_r(value) for value in per_trade["result_r"]],
                    tr("Post-close drawdown"): [format_currency(-Decimal(value), currency) for value in per_trade["drawdown"]],
                }
            )
            with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
                st.markdown(f"**{tr('Closed-trade detail')}**")
                _render_help_popover("Dashboard balance, P&L, streaks, and drawdown all follow the current logical-trade grouping.")
            st.dataframe(
                trade_table,
                hide_index=True,
                width="stretch",
                height=min(300, 42 + max(1, len(trade_table)) * 35),
            )

    return account


def render_strategy_analytics(repo: SQLiteJournalRepository) -> None:
    """Compare accounts that deliberately share one strategy, without mixing currencies."""
    st.markdown('<div class="dashboard-kicker">STRATEGY RESEARCH</div>', unsafe_allow_html=True)
    st.subheader(tr("Strategy analytics"))
    st.caption(tr("Compare accounts using one trading system. Monetary results stay separated by account currency; no conversion or combined P&L is shown."))
    profiles = [profile for profile in repo.list_strategy_profiles() if profile.name != "Journal default"]
    if not profiles:
        st.info(tr("Create a strategy, then bind it to an account in Settings."))
        st.page_link("app_pages/settings.py", label=tr("Go to Settings"), icon=":material/settings:")
        return
    profile_by_id = {profile.id: profile for profile in profiles}
    selected_id = st.selectbox("Trading system", list(profile_by_id), format_func=lambda item: profile_by_id[item].name)
    accounts = [account for account in repo.list_mt5_accounts() if account.strategy_profile_id == selected_id]
    if not accounts:
        st.info(tr("No active accounts are bound to this trading system."))
        st.page_link("app_pages/settings.py", label=tr("Go to Settings"), icon=":material/settings:")
        return
    st.caption(tr("All-time logical-trade records are shown for each account."))
    rows: list[dict[str, object]] = []
    for account in accounts:
        report = build_dashboard_report(repo, account_id=account.id)
        rows.append({
            "Account": account.display_name,
            "Currency": account.account_currency,
            "Closed trades": report.trade_count,
            "Win rate": format_percent(report.win_rate),
            "Total R": "—" if report.total_r is None else format_r(report.total_r),
            "Realized P&L": format_currency(report.net_pnl, account.account_currency),
            "Profit factor": "No losses" if report.profit_factor is None else format_number(report.profit_factor, 2),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(tr("Use account-level Dashboard and Bearings for execution coaching. This page is a descriptive cross-account comparison of the selected system."))


def main() -> None:
    # Configure browser branding before authentication, database access, or any
    # other Streamlit command so loading and early-error paths never inherit
    # Streamlit's default page title.
    st.set_page_config(page_title="Trade Compass", page_icon=TRADE_COMPASS_ICON, layout="wide")
    if is_multiuser_mode():
        username = render_login_gate()
        if username is None:
            render_connection_recovery()
            return
    try:
        repo = repository()
    except JournalDatabaseResetRequiredError as error:
        if is_desktop_mode():
            st.set_page_config(page_title="Trade Compass recovery", page_icon=TRADE_COMPASS_ICON, layout="wide")
            st.title(tr("Trade Compass recovery"))
            st.error(str(error))
            st.caption(tr("Reset the local database to start a clean journal. This cannot be undone."))
            render_desktop_database_reset()
            print("Trade Compass reset recovery screen active.", flush=True)
            return
        st.error(str(error))
        st.code("make reset-db CONFIRM_RESET=yes", language="bash")
        return
    except Exception as error:
        if is_desktop_mode():
            render_desktop_database_diagnostic(error)
            return
        raise
    settings = repo.get_journal_settings()
    st.session_state.setdefault("display_language", settings.display_language)
    install_streamlit_translations()
    render_connection_recovery()
    render_pending_toast()
    if is_multiuser_mode():
        render_logout_control()
        if current_username() is None:
            # The sidebar logout button (st.sidebar.button(...)) clears auth state and
            # returns True in this SAME script run, with no rerun of its own - continuing
            # past this point would run the rest of main() (and page.run(), which calls
            # repository() again independently per-page) against an already-logged-out
            # session and crash instead of cleanly falling back to the login form.
            st.rerun()

    with st.sidebar:
        selected_language = st.selectbox(
            "Language",
            options=list(LANGUAGES),
            format_func=LANGUAGES.get,
            key="display_language",
            width="stretch",
        )
    if selected_language != settings.display_language:
        repo.configure_journal(
            reporting_time_basis=settings.reporting_time_basis,
            display_language=selected_language,
        )
        st.rerun()
    apply_application_style()
    render_build_info()

    # App branding with logo. A plain container coerced into a row via CSS (rather
    # than st.columns) keeps the logo beside the title at every width - st.columns
    # collapses to stacked full-width blocks below Streamlit's column-stacking
    # breakpoint, which put the logo on its own line above the title on phones.
    # Keeping real st.image/st.title calls (instead of hand-built HTML) also keeps
    # them visible to AppTest, which looks for those element types specifically.
    with st.container(key="trade-compass-brand"):
        st.image(TRADE_COMPASS_ICON, width=50)
        st.title(tr("Trade Compass"))
    render_trade_doctrine(tr("Survival · Consistency · Discipline"))

    review_count = 0
    monitor_alert_count = 0
    focus_ready = False
    active_account = repo.get_active_mt5_account()
    ongoing_count = 0 if active_account is None else len(repo.list_live_positions(active_account.id))
    database_path = getattr(repo, "database_path", None)
    if active_account is not None and database_path is not None:
        change_token = _database_change_token(database_path)
        review_count = _cached_review_queue_count(str(database_path), change_token, active_account.id)
        monitor_alert_count = sum(
            account.id == active_account.id and alert.severity in {"critical", "warning"}
            for account, alert in _cached_global_framework_alerts(str(database_path), change_token)
        )
        focus_ready = _cached_focus_ready_to_evaluate(str(database_path), change_token, active_account.id)
    review_title = f"{tr('Review')} ({review_count})" if review_count else tr("Review")
    # focus_ready's "(1)" lands on Monitor, not Improve: the coaching-focus resolve UI it
    # signals only renders on the Monitor tab / Dashboard widget, never on Improve's roadmap
    # checklist page - putting the badge there was a dead end.
    monitor_badge_count = monitor_alert_count + (1 if focus_ready else 0)
    monitor_title = f"{tr('Monitor')} ({monitor_badge_count})" if monitor_badge_count else tr("Monitor")
    ongoing_title = f"{tr('Ongoing')} ({ongoing_count})" if ongoing_count else tr("Ongoing")
    improve_title = tr("Improve")

    page = st.navigation(
        {
            "Workspace": [
                st.Page("app_pages/ongoing.py", title=ongoing_title, icon=":material/candlestick_chart:"),
                st.Page("app_pages/dashboard.py", title=tr("Dashboard"), icon=":material/dashboard:", default=True),
                # Analytics is not the current focus — hidden from the nav for now.
                # Re-add the line above (see app_pages/analytics.py / render_strategy_analytics) when it's prioritized.
                st.Page("app_pages/bearings_review.py", title=review_title, icon=":material/rate_review:"),
                st.Page("app_pages/bearings_monitor.py", title=monitor_title, icon=":material/monitoring:"),
                st.Page("app_pages/bearings_improve.py", title=improve_title, icon=":material/trending_up:"),
                st.Page("app_pages/settings.py", title=tr("Settings"), icon=":material/settings:"),
                st.Page("app_pages/guidance.py", title=tr("Guide"), icon=":material/menu_book:"),
            ],
        },
        position="sidebar",
    )
    page.run()
    render_global_framework_alert_bubble(repo)


if __name__ == "__main__":
    main()
