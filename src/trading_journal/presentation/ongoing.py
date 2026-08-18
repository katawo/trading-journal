"""Live, read-only exposure workspace kept separate from post-trade analysis."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from trading_journal.application.live_positions import LivePositionService
from trading_journal.infrastructure.sqlite_repository import AccountListItem, SQLiteJournalRepository
from trading_journal.presentation.formatting import format_currency, format_exposure_r


ONGOING_REFRESH_INTERVAL_SECONDS = 5


@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)
def render_ongoing_positions_page(repo: SQLiteJournalRepository) -> None:
    account = repo.get_active_mt5_account()
    st.markdown("#### Ongoing positions")
    st.caption("Live exposure is read-only and separate from closed-trade reporting, reviews, and framework scores.")
    if account is None:
        st.info("Add and select an MT5 account in Settings to monitor live positions.")
        return
    report = LivePositionService(repo).build_report(account.id)
    _render_status(report.status, report.detail)
    snapshot_text = "No snapshot" if report.snapshot_time is None else report.snapshot_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.caption(f"Snapshot: {snapshot_text} · This workspace refreshes every {ONGOING_REFRESH_INTERVAL_SECONDS} seconds.")
    risk_value, risk_delta, risk_color, risk_description = _risk_metric(report)
    unprotected_value, unprotected_delta, unprotected_color = _unprotected_metric(report)
    pnl_value, pnl_delta, pnl_color = _pnl_metric(report, account.account_currency)
    with st.container(horizontal=True, gap="small"):
        st.metric(
            "Unprotected",
            unprotected_value,
            unprotected_delta,
            delta_color=unprotected_color,
            delta_arrow="off",
            border=True,
        )
        st.metric(
            "Known open risk",
            risk_value,
            risk_delta,
            delta_color=risk_color,
            delta_arrow="off",
            delta_description=risk_description,
            border=True,
        )
        st.metric("Open positions", None if report.snapshot_time is None else str(len(report.positions)), border=True)
        st.metric(
            "Unrealized P&L",
            pnl_value,
            pnl_delta,
            delta_color=pnl_color,
            delta_arrow="off",
            delta_description="floating" if report.snapshot_time is not None else None,
            border=True,
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
        return None, "Unavailable", "gray"
    if report.unprotected_count:
        detail = "Position needs a stop" if report.unprotected_count == 1 else "Positions need stops"
        return str(report.unprotected_count), detail, "red"
    if report.positions:
        return "0", "All protected", "green"
    return "0", "No open positions", "gray"


def _risk_metric(report) -> tuple[str | None, str, str, str | None]:
    limit_description = None if report.limit_r is None else f"{format_exposure_r(report.limit_r)} account limit"
    value = None if report.total_risk_r is None else format_exposure_r(report.total_risk_r)
    if report.snapshot_time is None:
        return value, "Unavailable", "gray", limit_description
    if report.status == "stop":
        label = "Limit reached" if report.limit_r == report.total_risk_r else "Over limit"
        return value, label, "red", limit_description
    if report.unprotected_count:
        label = "position" if report.unprotected_count == 1 else "positions"
        return value, f"{report.unprotected_count} {label} unavailable", "red", limit_description
    if report.status == "stale":
        return value, "Snapshot stale", "orange", limit_description
    if report.risk_unavailable_count:
        label = "position" if report.risk_unavailable_count == 1 else "positions"
        return value, f"{report.risk_unavailable_count} {label} risk unavailable", "orange", limit_description
    if not report.positions:
        return value, "No open risk", "gray", limit_description
    if report.status == "unconfigured" or value is None:
        return value, "Risk unavailable", "orange", limit_description
    if report.status == "caution":
        return value, "Near limit", "orange", limit_description
    return value, "Within limit", "green", limit_description


def _pnl_metric(report, currency: str) -> tuple[str | None, str, str]:
    if report.snapshot_time is None:
        return None, "Unavailable", "gray"
    value = format_currency(report.net_unrealized_pnl, currency)
    if report.net_unrealized_pnl > 0:
        return value, "Profit", "green"
    if report.net_unrealized_pnl < 0:
        return value, "Loss", "red"
    return value, "Flat", "gray"


def _render_positions(report, account: AccountListItem) -> None:
    st.markdown("##### Current positions")
    if report.snapshot_time is None:
        st.caption("Position data will appear after the first live MT5 snapshot.")
        return
    if not report.positions:
        st.info("No open positions in the latest live snapshot.")
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
    st.markdown("##### Live-risk incidents")
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
