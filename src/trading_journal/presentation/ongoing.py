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
from trading_journal.presentation.i18n import queue_toast, tr
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
    live_activity = _live_activity_indicator(report)
    st.markdown(
        '<div class="dashboard-period">'
        f'{live_activity}{escape(tr("Live"))} · {escape(tr("Snapshot"))} {escape(tr(snapshot_text))} · '
        f'{escape(tr("Refresh"))} {ONGOING_REFRESH_INTERVAL_SECONDS}s'
        "</div>",
        unsafe_allow_html=True,
    )
    risk_value, _, risk_color, risk_description = _risk_metric(report)
    unprotected_value, _, unprotected_color = _unprotected_metric(report)
    pnl_value, _, pnl_color = _pnl_metric(report, account.account_currency)
    risk_buffer_value, risk_buffer_detail, risk_buffer_tone = _risk_buffer_metric(report)
    exposure_column, positions_column = st.columns([1, 2.25], gap="small")
    with exposure_column.container(border=True, key="ongoing-exposure-snapshot"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown(f"#### {tr('Exposure snapshot')}")
            with st.popover("?", width="content"):
                st.caption(tr("Action-first live account risk. Floating values do not affect closed-trade reporting."))
        if report.status == "within":
            st.badge(tr("Within limit"), icon=":material/check_circle:", color="green")
        _render_compact_stack(
            (
                (
                    (tr("Unrealized P&L"), pnl_value, _METRIC_COLOR_TO_TONE[pnl_color], None),
                ),
                (
                    (
                        tr("Open positions"),
                        None if report.snapshot_time is None else str(len(report.positions)),
                        "info" if report.positions else "neutral",
                        None,
                    ),
                    (tr("Unprotected"), unprotected_value, _METRIC_COLOR_TO_TONE[unprotected_color], None),
                ),
                (
                    (tr("Known open risk"), risk_value, _METRIC_COLOR_TO_TONE[risk_color], risk_description),
                    (tr("Risk buffer"), risk_buffer_value, risk_buffer_tone, risk_buffer_detail),
                ),
            )
        )
    with positions_column:
        _render_positions(repo, report, account)


def _live_activity_indicator(report) -> str:  # type: ignore[no-untyped-def]
    """Show motion only while fresh open positions are being monitored."""

    if not report.positions or report.status == "stale":
        return ""
    return '<span class="ongoing-live-pulse" aria-hidden="true"></span>'


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


def _render_compact_stack(
    sections: tuple[tuple[tuple[str, str | None, str, str | None], ...], ...],
) -> None:
    st.markdown(
        '<div class="ongoing-exposure-stack">'
        + "".join(
            '<div class="ongoing-exposure-section dashboard-stat-list">'
            + "".join(
                _compact_stat_html(item, note_tone=item[2] if item_index == len(section) - 1 and section_index == len(sections) - 1 else None)
                for item_index, item in enumerate(section)
            )
            + "</div>"
            for section_index, section in enumerate(sections)
        )
        + "</div>",
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
                    st.badge(
                        tr("1 pos") if item.position_count == 1 else tr("{count} pos", count=item.position_count),
                        color="blue" if item.position_count > 1 else "gray",
                        icon=":material/layers:" if item.position_count > 1 else None,
                    )
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
        trade_column, coaching_column = st.columns([1.45, 1], gap="small")
        with trade_column:
            _render_today_trades(overview, account)
            _render_today_issues(overview)
        with coaching_column:
            _render_today_coaching(repo, account, framework, overview)
    render_post_trade_review_dialog(repo, account, framework)


def _render_positions(repo: SQLiteJournalRepository, report, account: AccountListItem) -> None:  # type: ignore[no-untyped-def]
    with st.container(border=True, key="ongoing-current-positions"):
        st.markdown(f"#### {tr('Current logical trades')}")
        st.caption(tr("Concurrent positions can be grouped while raw position risk remains visible."))
        if not report.logical_trades:
            _render_inline_position_state(report)
            return
        selection_prefix = f"ongoing-position-select-{account.id}-"
        selectable_ids = {
            item.members[0].position_id
            for item in report.logical_trades
            if not item.is_group and item.open_count == 1
        }
        for key in [key for key in st.session_state if key.startswith(selection_prefix)]:
            if key.removeprefix(selection_prefix) not in selectable_ids:
                st.session_state.pop(key, None)
        selected: list[str] = []
        grouping_enabled = report.snapshot_time is not None and report.status != "stale"
        for item in report.logical_trades:
            with st.container(border=True):
                title, action = st.columns([5, 1], vertical_alignment="center")
                with title:
                    if item.is_group:
                        st.markdown(f"**LT-{item.logical_trade_id} · {item.display_label}**")
                    else:
                        position_id = item.members[0].position_id
                        checked = st.checkbox(
                            f"{item.display_label} · {item.symbol} {item.direction.title()}",
                            key=f"{selection_prefix}{position_id}",
                            disabled=not grouping_enabled,
                        )
                        if checked:
                            selected.append(position_id)
                with action:
                    if item.is_group and st.button(
                        tr("Manage"),
                        key=f"ongoing-manage-group-{account.id}-{item.logical_trade_id}",
                        icon=":material/edit:",
                        disabled=not grouping_enabled,
                        width="stretch",
                    ):
                        st.session_state[f"ongoing-group-editor-{account.id}"] = {
                            "logical_trade_id": item.logical_trade_id,
                        }
                        st.rerun()
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    position_count = len(item.members)
                    st.badge(item.direction.title(), color="blue")
                    st.badge(
                        tr("{open}/{total} open", open=item.open_count, total=position_count),
                        color="green" if item.open_count == position_count else "orange",
                    )
                    st.badge(
                        _logical_trade_risk_label(item),
                        color="red" if item.unprotected_count else "orange" if item.risk_unavailable_count else "green",
                    )
                    st.badge(
                        format_currency(item.net_unrealized_pnl, account.account_currency),
                        color="green" if item.net_unrealized_pnl > 0 else "red" if item.net_unrealized_pnl < 0 else "gray",
                    )
                awaiting = sum(member.state == "awaiting_import" for member in item.members)
                if awaiting:
                    st.caption(tr("{count} closed position(s) are awaiting completed-history import.", count=awaiting))
                with st.expander(tr("Position details ({count})", count=position_count)):
                    st.dataframe(
                        pd.DataFrame([_logical_trade_member_row(member, account) for member in item.members]),
                        hide_index=True,
                        width="stretch",
                    )
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            if st.button(
                tr("Group selected ({count})", count=len(selected)),
                key=f"ongoing-create-group-{account.id}",
                icon=":material/group_work:",
                type="primary",
                disabled=not grouping_enabled or len(selected) < 2,
            ):
                st.session_state[f"ongoing-group-editor-{account.id}"] = {
                    "logical_trade_id": None,
                    "position_ids": tuple(selected),
                }
                st.rerun()
            if st.button(
                tr("Clear selection"),
                key=f"ongoing-clear-group-selection-{account.id}",
                icon=":material/clear:",
                disabled=not selected,
            ):
                for position_id in selectable_ids:
                    st.session_state.pop(f"{selection_prefix}{position_id}", None)
                st.rerun()
    _render_live_logical_trade_dialog(repo, account, report)


def _logical_trade_risk_label(item) -> str:  # type: ignore[no-untyped-def]
    if item.unprotected_count:
        return tr("Unprotected")
    if item.risk_unavailable_count or item.open_risk_r is None:
        return tr("Risk unavailable")
    return format_exposure_r(item.open_risk_r)


def _logical_trade_member_row(member, account: AccountListItem) -> dict[str, str]:  # type: ignore[no-untyped-def]
    if member.live is None:
        return {
            tr("Position"): member.position_id,
            tr("State"): tr("Closed") if member.state == "closed" else tr("Awaiting import"),
            tr("Volume"): "—",
            tr("Entry"): "—",
            tr("Current"): "—",
            tr("Stop"): "—",
            tr("Open risk"): "—",
            tr("Unrealized P&L"): "—",
            tr("Magic"): "—",
        }
    item = member.live
    position = item.position
    return {
        tr("Position"): position.position_id,
        tr("State"): tr("Open"),
        tr("Volume"): position.volume,
        tr("Entry"): position.entry_price,
        tr("Current"): position.current_price,
        tr("Stop"): position.stop_price or "—",
        tr("Open risk"): _risk_label(item.protected, item.risk_r),
        tr("Unrealized P&L"): format_currency(position.net_unrealized_pnl, account.account_currency),
        tr("Magic"): position.magic_number or "—",
    }


def _clear_live_logical_trade_dialog(account_id: int) -> None:
    st.session_state.pop(f"ongoing-group-editor-{account_id}", None)
    for key in [
        key for key in st.session_state
        if key.startswith(f"ongoing-group-dialog-{account_id}-")
    ]:
        st.session_state.pop(key, None)


def _render_live_logical_trade_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    report,
) -> None:  # type: ignore[no-untyped-def]
    editor = st.session_state.get(f"ongoing-group-editor-{account.id}")
    if editor is None:
        return
    st.dialog(
        tr("Manage live logical trade"),
        width="large",
        on_dismiss=lambda: _clear_live_logical_trade_dialog(account.id),
    )(_render_live_logical_trade_dialog_body)(repo, account, report, editor)


def _render_live_logical_trade_dialog_body(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    report,
    editor: dict,
) -> None:  # type: ignore[type-arg,no-untyped-def]
    service = LivePositionService(repo)
    logical_trade_id = editor.get("logical_trade_id")
    pending = {
        item.logical_trade_id: item
        for item in repo.list_pending_logical_trades(account.id)
    }
    live_by_id = {item.position.position_id: item for item in report.positions}
    grouped_ids = {
        position_id
        for item in pending.values()
        for position_id in item.position_ids
    }
    if logical_trade_id is None:
        selected_ids = tuple(position_id for position_id in editor.get("position_ids", ()) if position_id in live_by_id)
        if len(selected_ids) < 2:
            st.error(tr("Selected positions changed; refresh Ongoing and select them again"))
            return
        st.caption(tr("Confirm that these concurrently open positions represent one trading decision."))
        options = selected_ids
        default = selected_ids
        custom_label = ""
        fixed_ids: tuple[str, ...] = ()
    else:
        group = pending.get(logical_trade_id)
        if group is None:
            st.info(tr("This logical trade has completed or is no longer available to edit."))
            return
        first = next((item.position for item in report.positions if item.position.position_id in group.position_ids), None)
        if first is None:
            first_symbol, first_direction = group.symbol, group.direction
        else:
            first_symbol, first_direction = first.symbol, first.direction
        current_group_open_ids = {
            position_id for position_id in group.position_ids if position_id in live_by_id
        }
        compatible = [
            item.position.position_id
            for item in report.positions
            if item.position.symbol == first_symbol
            and item.position.direction == first_direction
            and (item.position.position_id not in grouped_ids or item.position.position_id in group.position_ids)
            and (bool(current_group_open_ids) or item.position.position_id in group.position_ids)
        ]
        options = tuple(compatible)
        default = tuple(position_id for position_id in group.position_ids if position_id in live_by_id)
        fixed_ids = tuple(position_id for position_id in group.position_ids if position_id not in live_by_id)
        custom_label = group.display_label or ""
        st.caption(tr("Closed or closing members stay fixed; currently open members can be added or removed."))
        if fixed_ids:
            st.caption(tr("Fixed members: {positions}", positions=", ".join(f"#{item}" for item in fixed_ids)))
    labels = {
        position_id: (
            f"#{position_id} · {live_by_id[position_id].position.symbol} "
            f"{live_by_id[position_id].position.direction} · "
            f"{format_currency(live_by_id[position_id].position.net_unrealized_pnl, account.account_currency)}"
        )
        for position_id in options
    }
    dialog_key = f"ongoing-group-dialog-{account.id}-{logical_trade_id}"
    label_key = f"{dialog_key}-label"
    members_key = f"{dialog_key}-members"
    st.session_state.setdefault(label_key, custom_label)
    label = st.text_input(
        tr("Trade label (optional)"),
        max_chars=160,
        placeholder=tr("e.g. London breakout scale-in"),
        key=label_key,
    )
    if logical_trade_id is None:
        chosen = list(default)
    else:
        stored_members = st.session_state.get(members_key)
        if stored_members is None:
            st.session_state[members_key] = list(default)
        else:
            st.session_state[members_key] = [
                position_id for position_id in stored_members if position_id in options
            ]
        chosen = st.multiselect(
            tr("Open positions"),
            options=list(options),
            format_func=labels.get,
            key=members_key,
        )
    preview_ids = tuple(chosen)
    if preview_ids:
        preview_items = [live_by_id[position_id] for position_id in preview_ids]
        st.dataframe(
            pd.DataFrame([
                {
                    tr("Position"): item.position.position_id,
                    tr("Symbol"): item.position.symbol,
                    tr("Side"): item.position.direction.title(),
                    tr("Open risk"): _risk_label(item.protected, item.risk_r),
                    tr("Unrealized P&L"): format_currency(
                        item.position.net_unrealized_pnl, account.account_currency
                    ),
                }
                for item in preview_items
            ]),
            hide_index=True,
            width="stretch",
        )
        preview_risk = (
            tr("Unprotected") if any(not item.protected for item in preview_items)
            else tr("Risk unavailable") if any(item.risk_r is None for item in preview_items)
            else format_exposure_r(sum((item.risk_r for item in preview_items if item.risk_r is not None), Decimal("0")))
        )
        preview_pnl = format_currency(
            sum((Decimal(item.position.net_unrealized_pnl) for item in preview_items), Decimal("0")),
            account.account_currency,
        )
        st.caption(tr("Current aggregate: {risk} open risk · {pnl} unrealized P&L", risk=preview_risk, pnl=preview_pnl))
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        save = st.button(
            tr("Confirm grouping") if logical_trade_id is None else tr("Save logical trade"),
            type="primary",
            icon=":material/group_work:",
            key=f"{dialog_key}-save",
        )
        can_disband = logical_trade_id is not None and not fixed_ids
        disband = st.button(
            tr("Disband into standalone positions"),
            icon=":material/call_split:",
            disabled=not can_disband,
            key=f"{dialog_key}-disband",
        ) if logical_trade_id is not None else False
    if not save and not disband:
        return
    try:
        if disband:
            service.disband_logical_trade(account.id, logical_trade_id)
            notice = tr("Live logical trade disbanded.")
        elif logical_trade_id is None:
            service.create_logical_trade(account.id, tuple(chosen), label)
            notice = tr("Live logical trade created.")
        else:
            service.update_logical_trade(account.id, logical_trade_id, tuple(chosen), label)
            notice = tr("Live logical trade saved.")
    except ValueError as error:
        st.error(tr(str(error)))
        return
    _clear_live_logical_trade_dialog(account.id)
    for position_id in live_by_id:
        st.session_state.pop(f"ongoing-position-select-{account.id}-{position_id}", None)
    queue_toast(notice)
    st.rerun()


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
