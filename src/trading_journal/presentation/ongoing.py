"""Live, read-only exposure workspace kept separate from post-trade analysis."""

from __future__ import annotations

from decimal import Decimal
from html import escape

import pandas as pd
import streamlit as st

from trading_journal.application.dashboard import DashboardService
from trading_journal.application.live_positions import LivePositionService
from trading_journal.infrastructure.sqlite_repository import AccountListItem, SQLiteJournalRepository
from trading_journal.presentation.formatting import format_currency, format_exposure_r, format_percent
from trading_journal.presentation.i18n import tr


ONGOING_REFRESH_INTERVAL_SECONDS = 5

_METRIC_COLOR_TO_TONE = {
    "gray": "neutral",
    "red": "negative",
    "green": "positive",
    "orange": "warning",
}


@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)
def render_ongoing_positions_page(repo: SQLiteJournalRepository) -> None:
    account = repo.get_active_mt5_account()
    _render_header(account)
    if account is None:
        st.info(tr("Add and select an MT5 account in Settings to monitor live positions."))
        return
    report = LivePositionService(repo).build_report(account.id)
    if report.status != "within":
        _render_status(report.status, report.detail)
    snapshot_text = "No snapshot" if report.snapshot_time is None else report.snapshot_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.markdown(
        '<div class="dashboard-period">'
        f'{escape(tr("Live"))} · {escape(tr("Snapshot"))} {escape(tr(snapshot_text))} · '
        f'{escape(tr("Refresh"))} {ONGOING_REFRESH_INTERVAL_SECONDS}s'
        "</div>",
        unsafe_allow_html=True,
    )
    risk_value, _, risk_color, risk_description = _risk_metric(report)
    unprotected_value, _, unprotected_color = _unprotected_metric(report)
    pnl_value, _, pnl_color = _pnl_metric(report, account.account_currency)
    dashboard = DashboardService(repo)
    today_pnl = dashboard.realized_pnl_on(dashboard.current_report_date(account.id), account.id)
    today_pnl_color = _realized_pnl_color(today_pnl)
    risk_buffer_value, risk_buffer_detail, risk_buffer_tone = _risk_buffer_metric(report)
    with st.container(border=True, key="ongoing-exposure-snapshot"):
        header, status = st.columns([3, 2], vertical_alignment="center")
        with header:
            st.markdown(f"#### {tr('Exposure snapshot')}")
            st.caption(tr("Action-first live account risk. Floating values do not affect closed-trade reporting."))
        with status:
            if report.status == "within":
                with st.container(horizontal_alignment="right", gap=None):
                    st.badge(tr("Within limit"), icon=":material/check_circle:", color="green")
                    st.caption(tr(report.detail), text_alignment="right")
        _render_compact_columns(
            pnl_items=[
                (tr("Unrealized P&L"), pnl_value, _METRIC_COLOR_TO_TONE[pnl_color], None),
                (
                    tr("Today realized P&L"),
                    format_currency(today_pnl, account.account_currency),
                    _METRIC_COLOR_TO_TONE[today_pnl_color],
                    None,
                ),
            ],
            position_items=[
                (
                    tr("Open positions"),
                    None if report.snapshot_time is None else str(len(report.positions)),
                    "info" if report.positions else "neutral",
                    None,
                ),
                (tr("Unprotected"), unprotected_value, _METRIC_COLOR_TO_TONE[unprotected_color], None),
            ],
            risk_item=(tr("Known open risk"), risk_value, _METRIC_COLOR_TO_TONE[risk_color], risk_description),
            buffer_item=(tr("Risk buffer"), risk_buffer_value, risk_buffer_tone, risk_buffer_detail),
        )
        if not report.positions:
            _render_inline_position_state(report)
    if report.positions:
        _render_positions(report, account)
    _render_incidents(repo, account)


def _render_header(account: AccountListItem | None) -> None:
    st.markdown(
        f'<div class="dashboard-kicker">{escape(tr("Live risk monitor"))}</div>',
        unsafe_allow_html=True,
    )
    st.subheader(tr("Ongoing positions"))
    if account is not None:
        st.caption(f"{account.display_name} · {account.login} · {account.account_currency} · {account.broker_server}")
    st.caption(tr("Live exposure is read-only and separate from closed-trade reporting, reviews, and framework scores."))


def _compact_stat_html(item: tuple[str, str | None, str, str | None]) -> str:
    label, value, tone, detail = item
    return (
        '<div class="dashboard-stat">'
        f'<div class="dashboard-stat-label">{escape(label)}</div>'
        f'<div class="dashboard-stat-value dashboard-stat-tone-{tone}">{escape(value or "—")}</div>'
        + ("" if detail is None else f'<div class="dashboard-stat-note">{escape(detail)}</div>')
        + "</div>"
    )


def _render_compact_columns(
    *,
    pnl_items: list[tuple[str, str | None, str, str | None]],
    position_items: list[tuple[str, str | None, str, str | None]],
    risk_item: tuple[str, str | None, str, str | None],
    buffer_item: tuple[str, str | None, str, str | None],
) -> None:
    pnl = "".join(_compact_stat_html(item) for item in pnl_items)
    positions = "".join(_compact_stat_html(item) for item in position_items)
    risk = _compact_stat_html(risk_item)
    buffer = _compact_stat_html(buffer_item)
    st.markdown(
        '<div class="ongoing-exposure-columns">'
        f'<div class="ongoing-exposure-column dashboard-stat-list">{pnl}</div>'
        f'<div class="ongoing-exposure-column dashboard-stat-list">{positions}</div>'
        f'<div class="ongoing-exposure-column ongoing-risk-column">{risk}</div>'
        f'<div class="ongoing-exposure-column ongoing-risk-column">{buffer}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _render_inline_position_state(report) -> None:
    if report.snapshot_time is None:
        message = tr("Position data will appear after the first live MT5 snapshot.")
        st.markdown(f":material/schedule: **{message}**")
        return
    if report.status == "stale":
        message = tr("Snapshot stale")
        detail = tr("Latest position state is unavailable until a fresh MT5 snapshot arrives.")
        st.markdown(f":material/warning: **{message}.** {detail}")
        return
    message = tr("No open positions in the latest live snapshot.")
    detail = tr("The position table appears when MT5 reports an open trade.")
    st.markdown(f":material/check_circle: **{message}** {detail}")


def _render_status(status: str, detail: str) -> None:
    if status in {"stop", "unprotected", "stale"}:
        st.error(detail, icon=":material/error:")
    elif status in {"caution", "risk_unavailable"}:
        st.warning(detail, icon=":material/warning:")
    elif status in {"waiting", "unconfigured"}:
        st.info(detail, icon=":material/info:")
    else:
        st.success(detail, icon=":material/check_circle:")


def _unprotected_metric(report) -> tuple[str | None, str, str]:
    if report.snapshot_time is None:
        return None, tr("Unavailable"), "gray"
    if report.unprotected_count:
        detail = tr("Position needs a stop") if report.unprotected_count == 1 else tr("Positions need stops")
        return str(report.unprotected_count), detail, "red"
    if report.positions:
        return "0", tr("All protected"), "green"
    return "0", tr("No open positions"), "gray"


def _risk_metric(report) -> tuple[str | None, str, str, str | None]:
    limit_description = None if report.limit_r is None else tr("{limit} account limit", limit=format_exposure_r(report.limit_r))
    value = None if report.total_risk_r is None else format_exposure_r(report.total_risk_r)
    if report.snapshot_time is None:
        return value, tr("Unavailable"), "gray", limit_description
    if report.status == "stop":
        label = tr("Limit reached") if report.limit_r == report.total_risk_r else tr("Over limit")
        return value, label, "red", limit_description
    if report.unprotected_count:
        label = tr("position") if report.unprotected_count == 1 else tr("positions")
        return value, tr("{count} {label} unavailable", count=report.unprotected_count, label=label), "red", limit_description
    if report.status == "stale":
        return value, tr("Snapshot stale"), "orange", limit_description
    if report.risk_unavailable_count:
        label = tr("position") if report.risk_unavailable_count == 1 else tr("positions")
        return value, tr("{count} {label} risk unavailable", count=report.risk_unavailable_count, label=label), "orange", limit_description
    if not report.positions:
        return value, tr("No open risk"), "gray", limit_description
    if report.status == "unconfigured" or value is None:
        return value, tr("Risk unavailable"), "orange", limit_description
    if report.status == "caution":
        return value, tr("Near limit"), "orange", limit_description
    return value, tr("Within limit"), "green", limit_description


def _pnl_metric(report, currency: str) -> tuple[str | None, str, str]:
    if report.snapshot_time is None:
        return None, tr("Unavailable"), "gray"
    value = format_currency(report.net_unrealized_pnl, currency)
    if report.net_unrealized_pnl > 0:
        return value, tr("Profit"), "green"
    if report.net_unrealized_pnl < 0:
        return value, tr("Loss"), "red"
    return value, tr("Flat"), "gray"


def _realized_pnl_color(value: str) -> str:
    pnl = Decimal(value)
    if pnl > 0:
        return "green"
    if pnl < 0:
        return "red"
    return "gray"


def _risk_buffer_metric(report) -> tuple[str | None, str, str]:
    if report.snapshot_time is None:
        return None, tr("Unavailable"), "neutral"
    if report.status == "stale":
        return None, tr("Snapshot stale"), "warning"
    if report.limit_r is None or report.limit_r <= 0:
        return None, tr("Limit unavailable"), "warning"
    if report.unprotected_count or report.risk_unavailable_count or report.total_risk_r is None:
        tone = "negative" if report.unprotected_count else "warning"
        return None, tr("Known risk is a lower bound"), tone
    utilization = report.total_risk_r * Decimal("100") / report.limit_r
    buffer = max(Decimal("0"), report.limit_r - report.total_risk_r)
    displayed_utilization = utilization.quantize(Decimal("0.1"))
    tone = "negative" if displayed_utilization >= 100 else "warning" if displayed_utilization >= 80 else "positive"
    decimal_places = 0 if displayed_utilization == displayed_utilization.to_integral_value() else 1
    detail = tr("{percent} of limit used", percent=format_percent(displayed_utilization, decimal_places=decimal_places))
    return format_exposure_r(buffer), detail, tone


def _render_positions(report, account: AccountListItem) -> None:
    with st.container(border=True, key="ongoing-current-positions"):
        st.markdown(f"#### {tr('Current positions')}")
        st.caption(tr("Highest-risk and unprotected positions remain first."))
        rows = []
        for item in sorted(report.positions, key=_position_priority):
            position = item.position
            rows.append({
                "Position": position.position_id,
                "Symbol": position.symbol,
                "Side": position.direction.title(),
                "Volume": position.volume,
                "Entry": position.entry_price,
                "Current": position.current_price,
                "Stop": position.stop_price or "—",
                "Open risk": _risk_label(item.protected, item.risk_r),
                "Unrealized P&L": format_currency(position.net_unrealized_pnl, account.account_currency),
                "Magic": position.magic_number or "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _position_priority(item) -> tuple[int, Decimal, str, str]:
    position = item.position
    if not item.protected:
        return (0, Decimal("0"), position.symbol, position.position_id)
    if item.risk_r is not None:
        return (1, -Decimal(item.risk_r), position.symbol, position.position_id)
    return (2, Decimal("0"), position.symbol, position.position_id)


def _risk_label(protected: bool, risk_r) -> str:
    if not protected:
        return "Unprotected"
    return "—" if risk_r is None else format_exposure_r(risk_r)


def _render_incidents(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    incidents = repo.list_live_position_incidents(account.id)
    if not incidents:
        return
    with st.container(border=True, key="ongoing-live-risk-incidents"):
        st.markdown(f"#### {tr('Live-risk incidents')}")
        st.caption(tr("Recent state transitions only; newest event first."))
        frame = pd.DataFrame([
            {
                "Time": incident.occurred_at,
                "Event": incident.category.replace("_", " ").title(),
                "State": incident.state.title(),
                "Position": incident.position_id or "Account",
                "Detail": incident.detail,
            }
            for incident in incidents
        ])
        st.dataframe(frame, hide_index=True, width="stretch")
