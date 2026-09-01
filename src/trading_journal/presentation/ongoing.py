"""Ongoing workspace for live exposure and the current reporting day."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from trading_journal.application.framework import FrameworkService, PILLAR_NAMES
from trading_journal.application.live_positions import LivePositionService
from trading_journal.application.today import TodayOverview, TodayService, TodayTradeSummary
from trading_journal.infrastructure.sqlite_repository import AccountListItem, SQLiteJournalRepository
from trading_journal.presentation.browser_timezone import browser_timezone
from trading_journal.presentation.formatting import format_currency, format_exposure_r, format_percent, format_r
from trading_journal.presentation.framework import (
    HARD_RULE_LABELS,
    VIOLATION_LABELS,
    render_compact_framework_focus,
    render_post_trade_review_dialog,
)
from trading_journal.presentation.i18n import tr
from trading_journal.presentation.trade_tags import direction_tag, outcome_tag


ONGOING_REFRESH_INTERVAL_SECONDS = 5

_METRIC_COLOR_TO_TONE = {
    "gray": "neutral",
    "red": "negative",
    "green": "positive",
    "orange": "warning",
}


def render_ongoing_positions_page(repo: SQLiteJournalRepository) -> None:
    account = repo.get_active_mt5_account()
    _render_header(account)
    if account is None:
        st.info(tr("Add and select an MT5 account in Settings to monitor live positions."))
        return
    _render_live_positions(repo, account)
    _render_today_action_center(repo, account)
    _render_incidents(repo, account)


@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)
def _render_live_positions(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
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


def _render_header(account: AccountListItem | None) -> None:
    st.markdown(
        f'<div class="dashboard-kicker">{escape(tr("Live risk monitor"))}</div>',
        unsafe_allow_html=True,
    )
    st.subheader(tr("Ongoing workspace"))
    if account is not None:
        st.caption(f"{account.display_name} · {account.login} · {account.account_currency} · {account.broker_server}")
    st.caption(tr("Live exposure and today's closed-trade workflow share one workspace. P&L remains separate from process quality."))


def _compact_stat_html(
    item: tuple[str, str | None, str, str | None],
    *,
    note_tone: str | None = None,
) -> str:
    label, value, tone, detail = item
    note_classes = "dashboard-stat-note"
    if note_tone is not None:
        note_classes += f" dashboard-stat-tone-{note_tone}"
    return (
        '<div class="dashboard-stat">'
        f'<div class="dashboard-stat-label">{escape(label)}</div>'
        f'<div class="dashboard-stat-value dashboard-stat-tone-{tone}">{escape(value or "—")}</div>'
        + ("" if detail is None else f'<div class="{note_classes}">{escape(detail)}</div>')
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
    buffer = _compact_stat_html(buffer_item, note_tone=buffer_item[2])
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


def _reporting_basis_label(value: str) -> str:
    return {
        "server": tr("MT5 server time"),
        "utc": "UTC",
        "local": tr("local computer time"),
    }[value]


def _open_today_review(trade_id: int, queue: tuple[int, ...] = ()) -> None:
    st.session_state["post-trade-review-trade-id"] = trade_id
    st.session_state["post-trade-review-queue"] = queue


def _render_today_metrics(overview: TodayOverview, account: AccountListItem) -> None:
    pnl_tone = _METRIC_COLOR_TO_TONE[_realized_pnl_color(overview.realized_pnl)]
    if overview.daily_r is None:
        daily_r = (tr("Daily R"), None, "warning", tr("Risk unavailable"))
    else:
        daily_r_value = Decimal(overview.daily_r)
        daily_r_tone = "positive" if daily_r_value > 0 else "negative" if daily_r_value < 0 else "neutral"
        daily_r = (tr("Daily R"), format_r(overview.daily_r), daily_r_tone, tr("Risk-normalized outcome"))
    review_tone = "neutral" if not overview.trades else "warning" if overview.pending_count else "positive"
    review_detail = (
        tr("No closed trades")
        if not overview.trades
        else tr("{count} pending", count=overview.pending_count)
        if overview.pending_count
        else tr("All reviewed")
    )
    metrics = (
        (
            tr("Today realized P&L"),
            format_currency(overview.realized_pnl, account.account_currency),
            pnl_tone,
            tr("Outcome only"),
        ),
        daily_r,
        (tr("Closed logical trades"), str(len(overview.trades)), "info" if overview.trades else "neutral", None),
        (tr("Reviews"), f"{overview.reviewed_count}/{len(overview.trades)}", review_tone, review_detail),
    )
    st.markdown(
        '<div class="ongoing-today-columns">'
        + "".join(f'<div class="ongoing-today-column">{_compact_stat_html(item)}</div>' for item in metrics)
        + "</div>",
        unsafe_allow_html=True,
    )


def _review_label(item: TodayTradeSummary) -> tuple[str, str]:
    return {
        "needs_approval": (tr("Requires review"), "red"),
        "auto_review": (tr("Awaiting approval"), "orange"),
        "approved_auto_review": (tr("Auto"), "green"),
        "manual_review": (tr("Manual"), "green"),
    }.get(item.review_kind, (tr("Pending review"), "orange"))


def _render_today_trades(overview: TodayOverview, account: AccountListItem) -> None:
    st.markdown("##### " + tr("Today's trades"))
    if not overview.trades:
        st.caption(tr("No logical trades have closed in this reporting day."))
        return
    for item in overview.trades:
        with st.container(border=True):
            detail, action = st.columns([4, 1], vertical_alignment="center")
            direction = direction_tag(item.direction)
            outcome = outcome_tag(item.net_pnl)
            review_label, review_color = _review_label(item)
            with detail:
                st.markdown(f"**LT-{item.trade_id} · {item.symbol}**")
                closed_at = datetime.fromisoformat(item.closed_at).strftime("%H:%M")
                st.caption(f"{tr('Closed')} {closed_at} · {item.display_label}")
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.badge(tr(direction.label), color=direction.color, icon=direction.icon)
                    st.badge(format_currency(item.net_pnl, account.account_currency), color=outcome.color)
                    st.badge(review_label, color=review_color)
                    if item.classification is not None:
                        classification_color = (
                            "green" if item.classification.startswith("Good") else
                            "red" if item.classification.startswith("Bad") else "orange"
                        )
                        st.badge(tr(item.classification), color=classification_color)
                issue_labels = [tr(VIOLATION_LABELS.get(code, code)) for code in item.violation_codes]
                hard_labels = [tr(HARD_RULE_LABELS.get(code, code)) for code in item.hard_rule_codes]
                if issue_labels or hard_labels:
                    st.caption(" · ".join((*issue_labels, *hard_labels)))
            with action:
                st.button(
                    tr("Edit review") if item.reviewed else tr("Review"),
                    key=f"ongoing-today-review-{account.id}-{item.trade_id}",
                    type="secondary" if item.reviewed else "primary",
                    icon=":material/edit:",
                    width="stretch",
                    on_click=_open_today_review,
                    args=(item.trade_id, ()),
                )


def _render_today_issues(overview: TodayOverview) -> None:
    st.markdown("##### " + tr("Today's reviewed issues"))
    if not overview.trades:
        st.caption(tr("No closed trades are available for review today."))
        return
    if overview.reviewed_count == 0:
        st.info(tr("Reviews are still pending; no conclusion about mistakes is available yet."), icon=":material/pending_actions:")
        return
    issue_columns = st.columns(2, gap="small")
    with issue_columns[0]:
        st.markdown(f"**{tr('Mistakes')}**")
        if overview.mistakes:
            for issue in overview.mistakes:
                st.badge(f"{tr(VIOLATION_LABELS.get(issue.code, issue.code))} · {issue.count}", color="orange")
        else:
            st.caption(tr("No reviewed mistakes were recorded for today's trades."))
    with issue_columns[1]:
        st.markdown(f"**{tr('Hard-rule events')}**")
        if overview.hard_rules:
            for issue in overview.hard_rules:
                st.badge(f"{tr(HARD_RULE_LABELS.get(issue.code, issue.code))} · {issue.count}", color="red")
        else:
            st.caption(tr("No hard-rule events were recorded for today's trades."))
    actions = [(item.trade_id, item.corrective_action) for item in overview.trades if item.corrective_action]
    st.markdown(f"**{tr('Corrective actions')}**")
    if actions:
        for trade_id, action in actions:
            st.markdown(f"**LT-{trade_id}**")
            st.write(action)
    else:
        st.caption(tr("No corrective actions were recorded for today's trades."))


def _render_today_coaching(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    framework: FrameworkService,
    overview: TodayOverview,
) -> None:
    st.markdown(f"##### {tr('Coaching today')}")
    render_compact_framework_focus(repo, account, framework)
    st.markdown(f"**{tr('Resolved today')}**")
    if not overview.resolved_focuses:
        st.caption(tr("No coaching focus was resolved today."))
        return
    for focus in overview.resolved_focuses:
        with st.container(border=True):
            status_color = "green" if focus.status == "completed" else "gray"
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.badge(tr(focus.status.capitalize()), color=status_color)
                st.badge(tr(PILLAR_NAMES[focus.pillar]), color="blue")
            st.write(tr(focus.action_text))
            if focus.resolution_note:
                st.caption(focus.resolution_note)


def _render_today_action_center(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    settings = repo.get_journal_settings()
    local_zone = browser_timezone() if settings.reporting_time_basis == "local" else None
    framework = FrameworkService(repo, local_zone=local_zone)
    framework.ensure_coaching_focus(account.id)
    overview = TodayService(repo, local_zone=local_zone, framework=framework).build(account.id)
    pending = tuple(item for item in reversed(overview.trades) if not item.reviewed)

    with st.container(border=True, key="ongoing-today-action-center"):
        heading, actions = st.columns([4, 1], vertical_alignment="center")
        with heading:
            st.markdown(f"#### {tr('Today action center')}")
            st.caption(
                tr(
                    "{date} · {basis} reporting calendar",
                    date=overview.report_date,
                    basis=_reporting_basis_label(overview.reporting_time_basis),
                )
            )
        with actions:
            if pending:
                st.button(
                    tr("Review pending ({count})", count=len(pending)),
                    key=f"ongoing-today-review-pending-{account.id}",
                    type="primary",
                    icon=":material/rate_review:",
                    width="stretch",
                    on_click=_open_today_review,
                    args=(pending[0].trade_id, tuple(item.trade_id for item in pending[1:])),
                )
        _render_today_metrics(overview, account)
        trade_column, coaching_column = st.columns([1.45, 1], gap="large")
        with trade_column:
            _render_today_trades(overview, account)
            _render_today_issues(overview)
        with coaching_column:
            _render_today_coaching(repo, account, framework, overview)
    render_post_trade_review_dialog(repo, account, framework)


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


@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)
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
