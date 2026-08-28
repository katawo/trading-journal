"""Live, read-only exposure workspace kept separate from post-trade analysis."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from trading_journal.application.live_positions import LivePositionService
from trading_journal.infrastructure.sqlite_repository import AccountListItem, SQLiteJournalRepository
from trading_journal.presentation.formatting import AccentMetricTone, format_currency, format_exposure_r, render_accent_metric
from trading_journal.presentation.i18n import tr


ONGOING_REFRESH_INTERVAL_SECONDS = 5

_METRIC_COLOR_TO_TONE: dict[str, AccentMetricTone] = {
    "gray": "neutral",
    "red": "negative",
    "green": "positive",
    "orange": "warning",
}


@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)
def render_ongoing_positions_page(repo: SQLiteJournalRepository) -> None:
    account = repo.get_active_mt5_account()
    st.markdown(tr("#### Ongoing positions"))
    st.caption(tr("Live exposure is read-only and separate from closed-trade reporting, reviews, and framework scores."))
    if account is None:
        st.info(tr("Add and select an MT5 account in Settings to monitor live positions."))
        return
    report = LivePositionService(repo).build_report(account.id)
    _render_status(report.status, report.detail)
    snapshot_text = "No snapshot" if report.snapshot_time is None else report.snapshot_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.caption(f"Snapshot: {snapshot_text} · This workspace refreshes every {ONGOING_REFRESH_INTERVAL_SECONDS} seconds.")
    risk_value, risk_delta, risk_color, risk_description = _risk_metric(report)
    unprotected_value, unprotected_delta, unprotected_color = _unprotected_metric(report)
    pnl_value, pnl_delta, pnl_color = _pnl_metric(report, account.account_currency)
    with st.container(horizontal=True, gap="small"):
        render_accent_metric(
            tr("Unprotected"),
            unprotected_value,
            key="ongoing-unprotected",
            tone=_METRIC_COLOR_TO_TONE[unprotected_color],
            delta=unprotected_delta,
            delta_color=unprotected_color,
            delta_arrow="off",
        )
        render_accent_metric(
            tr("Known open risk"),
            risk_value,
            key="ongoing-known-open-risk",
            tone=_METRIC_COLOR_TO_TONE[risk_color],
            delta=risk_delta,
            delta_color=risk_color,
            delta_arrow="off",
            delta_description=risk_description,
        )
        render_accent_metric(
            tr("Open positions"),
            None if report.snapshot_time is None else str(len(report.positions)),
            key="ongoing-open-positions",
            tone="neutral",
        )
        render_accent_metric(
            tr("Unrealized P&L"),
            pnl_value,
            key="ongoing-unrealized-pnl",
            tone=_METRIC_COLOR_TO_TONE[pnl_color],
            delta=pnl_delta,
            delta_color=pnl_color,
            delta_arrow="off",
            delta_description="floating" if report.snapshot_time is not None else None,
        )
    _render_positions(report, account)
    _render_incidents(repo, account)


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


def _render_positions(report, account: AccountListItem) -> None:
    st.markdown(tr("##### Current positions"))
    if report.snapshot_time is None:
        st.caption(tr("Position data will appear after the first live MT5 snapshot."))
        return
    if not report.positions:
        st.info(tr("No open positions in the latest live snapshot."))
        return
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
    st.markdown(tr("##### Live-risk incidents"))
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
