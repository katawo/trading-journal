"""Native Streamlit presentation for the greenfield three-pillar framework."""

from __future__ import annotations

from collections import Counter
from collections.abc import MutableMapping, Sequence
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import altair as alt

from trading_journal.application.framework import (
    PILLAR_NAMES,
    ROADMAP_LEVEL_NAMES,
    FrameworkService,
    MonitorAnalysisReport,
    PillarScore,
    RiskSnapshot,
    TradeProcessScore,
)
from trading_journal.application.reporting_time import reporting_datetime
from trading_journal.infrastructure.sqlite_repository import (
    ASSESSMENT_CRITERIA,
    PSYCHOLOGY_CRITERIA,
    RISK_CRITERIA,
    SYSTEM_CRITERIA,
    AccountListItem,
    ReviewContextSelection,
    SQLiteJournalRepository,
)
from trading_journal.presentation.i18n import tr
from trading_journal.presentation.formatting import format_currency, format_percent, format_r, format_score
from trading_journal.presentation.trade_tags import direction_tag, outcome_tag


GRADE_OPTIONS = ("Pass", "Partial", "Fail")
REVIEW_PAGE_SIZE = 25
CRITERIA_GRID_COLUMNS = 4
PILLAR_ACCENT_COLORS = {"Psychology": "blue", "Risk management": "orange", "Trading system": "violet"}
CRITERION_LABELS = {
    "rule_adherence": "Rule adherence",
    "impulse_control": "Impulse control",
    "emotional_control": "Emotional control",
    "patience_discipline": "Patience & discipline",
    "policy_adherence": "Risk-policy adherence",
    "position_size_accuracy": "Position-size accuracy",
    "stop_discipline": "Stop discipline",
    "exposure_limit_compliance": "Exposure & limit compliance",
    "setup_validity": "Setup validity",
    "context_alignment": "Context alignment",
    "entry_fidelity": "Entry fidelity",
    "invalidation_fidelity": "Invalidation / stop fidelity",
    "management_exit_fidelity": "Management / exit fidelity",
}
VIOLATION_LABELS = {
    "fomo_or_chase": "FOMO or chased price",
    "revenge": "Revenge behavior",
    "emotional_sizing": "Emotional position sizing",
    "post_loss_reset": "Poor post-loss reset",
    "daily_limit": "Daily limit issue",
    "weekly_limit": "Weekly limit issue",
    "drawdown_limit": "Drawdown limit issue",
    "open_exposure": "Open exposure issue",
    "correlation_exposure": "Correlation exposure issue",
    "stop_widened": "Stop widened",
    "mandatory_setup_absent": "Mandatory setup absent",
    "shutdown_breach": "Traded after hard shutdown",
}
HARD_RULE_LABELS = {
    "oversized_revenge": "Intentional oversized revenge trade",
    "mandatory_setup_absent": "Mandatory setup absent",
    "stop_widened": "Deliberately widened stop",
    "shutdown_breach": "Traded after hard shutdown",
}
AUTOMATIC_RISK_EVENT_LABELS = {
    "daily_limit": "Daily loss limit",
    "weekly_limit": "Weekly loss limit",
    "drawdown_limit": "Maximum drawdown limit",
    "loss_streak": "Maximum losing-streak limit",
}
COMPONENT_DEFINITIONS = {
    "Rule adherence": "Average reviewed Rule adherence grade.",
    "Impulse control": "Average reviewed Impulse control grade.",
    "Emotional control": "Average reviewed Emotional control grade.",
    "Post-loss discipline": "The next reviewed trade after a loss across all active accounts: its Impulse control grade, or 0 when tagged post_loss_reset. 100 when the sample has no eligible post-loss sequence.",
    "Policy adherence": "Average reviewed Policy adherence grade.",
    "Stop discipline": "Average reviewed Stop discipline grade.",
    "Limit compliance": "100 for a reviewed trade with no historical daily/weekly/drawdown/streak event; 0 when an event occurred.",
    "Exposure control": "Average reviewed Exposure-limit compliance grade.",
    "Setup validity": "Average reviewed Setup validity grade.",
    "Execution fidelity": "Average of Entry, Invalidation, and Management/exit grades.",
    "Context alignment": "Average reviewed Context alignment grade.",
    "Evidence quality": "100 when the attached strategy is documented with 100 or more backtest trades; 50 when documented with fewer; otherwise 0.",
    "Edge evidence": "100 for 100 or more backtest trades with positive expectancy after costs; 50 for 50 or more; otherwise 0.",
}


def _account_label(account: AccountListItem) -> str:
    return f"{account.display_name} · {account.login} · {account.broker_server}"


def _score_text(value: str | None) -> str:
    return "—" if value is None else format_score(value)


def _state_label(snapshot: RiskSnapshot) -> str:
    # "Elevated," not "Caution" — a pillar's rolling score can independently show
    # "Caution" (capped by repeated critical violations) at the same time this
    # metric is visible; reusing the same word for two unrelated states would
    # collide on screen.
    return tr({"clear": "Clear", "caution": "Elevated", "stop": "Stop", "unconfigured": "Set up"}[snapshot.state])


def _auto_risk_label(score: TradeProcessScore) -> str:
    state = {
        "within_policy": "Within policy",
        "over_policy": "Over policy",
        "unavailable": "Unavailable",
    }.get(score.risk_policy_state, "Unavailable")
    if score.risk_evidence_source == "reviewed_actual_risk":
        return f"{tr('Reviewed actual risk')} · {tr(state)}"
    evidence = score.auto_risk
    source = {
        "specific_preset_sl": "Preset SL",
        "real_loss_sl": "Real-loss estimate",
        "pretrade_account_balance_sl": "Pre-trade-balance estimate",
        "mixed_sources": "Mixed estimates",
        "unavailable": "No source",
    }.get(evidence.risk_basis, "No source")
    return f"{tr(source)} · {tr(state)}"


def _risk_evidence_detail(score: TradeProcessScore) -> str:
    if score.risk_evidence_source != "reviewed_actual_risk":
        return score.auto_risk.detail
    policy = (
        tr("Policy maximum: {amount}.", amount=score.policy_risk_amount)
        if score.policy_risk_amount is not None
        else tr("No policy maximum is available.")
    )
    state = {
        "within_policy": "within policy",
        "over_policy": "over policy",
        "unavailable": "not comparable with policy",
    }.get(score.risk_policy_state, "not comparable with policy")
    return tr(
        "Reviewed Actual risk {amount} is {state}. {policy} It replaces automatic evidence for this logical-trade policy comparison only; daily, weekly, drawdown, and streak monitoring remain based on immutable MT5 positions.",
        amount=score.actual_risk_amount,
        state=tr(state),
        policy=policy,
    )


def _process_failure_detail(score: TradeProcessScore) -> str | None:
    """Explain a hard process failure without conflating it with pillar grades."""
    if score.process_status != "FAIL":
        return None
    assessed_hard_rules = [
        HARD_RULE_LABELS.get(code, code.replace("_", " ").capitalize())
        for code in score.hard_rule_codes
    ]
    return tr(
        "Process failed — hard-rule event: {events}. This overrides the numeric score above; the pillar(s) it affects show FAIL regardless of their percentage.",
        events=", ".join(tr(item) for item in (assessed_hard_rules or ["recorded"])),
    )


def _automatic_risk_monitoring_detail(score: TradeProcessScore) -> str | None:
    """Describe advisory closed-position evidence and the next review action."""
    reached = [tr(AUTOMATIC_RISK_EVENT_LABELS[code]) for code in score.automatic_risk_event_codes]
    candidates = [tr(AUTOMATIC_RISK_EVENT_LABELS[code]) for code in score.shutdown_candidate_codes]
    details: list[str] = []
    if reached:
        details.append(tr("Risk monitor reached: {events}", events=", ".join(reached)))
    if candidates:
        details.append(
            tr(
                "Shutdown review: this entry followed an earlier completed position that reached {events}",
                events=", ".join(candidates),
            )
        )
    if not details:
        return None
    return tr("{details}. This is advisory evidence, not a Process failure by itself.", details=". ".join(details))


def _reporting_time(repo: SQLiteJournalRepository, value: str, server_utc_offset_minutes: int) -> str:
    """Show execution time in the same calendar used for reports and alerts."""
    basis = repo.get_journal_settings().reporting_time_basis
    return reporting_datetime(value, server_utc_offset_minutes, basis).strftime("%Y-%m-%d %H:%M:%S")


def _score_scope_label(score: PillarScore, account: AccountListItem) -> str:
    """Describe the evidence scope without implying that a pillar is an aggregate."""
    if score.pillar == "psychology":
        return tr("Trader-wide")
    label = _account_label(account)
    return tr("Account: {account}", account=label) if score.pillar == "risk" else tr("System: {account}", account=label)


def _render_score_cards(scores: tuple[PillarScore, ...], account: AccountListItem) -> None:
    for column, score in zip(st.columns(len(scores), gap="small"), scores, strict=True):
        if score.hard_block:
            label = "FAIL"
        elif score.score is None:
            label = "Incomplete"
        elif score.status == "incomplete":
            # A live percentage next to the literal word "Incomplete" reads as
            # self-contradictory — this is a partial-sample early read, not the
            # same "no evidence yet" state as score.score is None.
            label = "Early estimate"
        else:
            label = score.status.capitalize()
        column.metric(tr(PILLAR_NAMES[score.pillar]), _score_text(score.score), tr("{label} · {count} in sample", label=tr(label), count=score.sample_size), border=True)
        column.caption(_score_scope_label(score, account))
        if score.status != "ready":
            column.caption(tr(score.detail))


def _render_pillar_radar(scores: tuple[PillarScore, ...]) -> None:
    labels = [tr(PILLAR_NAMES[score.pillar]) for score in scores]
    values = [float(Decimal(score.score)) if score.score is not None else 0.0 for score in scores]
    # A hard-blocked pillar's score is not capped (only Caution caps at 59), so its
    # vertex can otherwise look just as healthy as a clean pillar. A Caution-capped
    # pillar has the same problem — 59% renders identically whether it's a genuine
    # score or a cap. Mark both distinctly so the chart never visually contradicts
    # the status shown on its card.
    blocked = [score.hard_block for score in scores]
    capped = [score.status == "caution" and not score.hard_block for score in scores]
    marker_colors = ["#d62728" if is_blocked else "#ffa15a" if is_capped else "#636efa" for is_blocked, is_capped in zip(blocked, capped, strict=True)]
    marker_symbols = ["x" if is_blocked else "diamond" if is_capped else "circle" for is_blocked, is_capped in zip(blocked, capped, strict=True)]
    figure = go.Figure(
        go.Scatterpolar(
            r=values + values[:1],
            theta=labels + labels[:1],
            fill="toself",
            marker=dict(color=marker_colors + marker_colors[:1], symbol=marker_symbols + marker_symbols[:1], size=10),
        )
    )
    figure.update_layout(
        height=260,
        margin=dict(l=24, r=24, t=24, b=24),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        polar=dict(radialaxis=dict(range=[0, 100], showticklabels=True, ticksuffix="%"), bgcolor="rgba(0,0,0,0)"),
    )
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    sample_line = "  ·  ".join(f"{tr(PILLAR_NAMES[score.pillar])} {score.sample_size}" for score in scores)
    st.caption(tr("Sample size: {sample_line}", sample_line=sample_line))
    if any(blocked) and any(capped):
        st.caption("Pillars marked ✕ in red have an active hard-rule failure; pillars marked ◆ in amber are capped by repeated critical violations — neither score reflects readiness.")
    elif any(blocked):
        st.caption("Pillars marked ✕ in red have an active hard-rule failure — their score does not reflect readiness.")
    elif any(capped):
        st.caption("Pillars marked ◆ in amber are capped at 59 by repeated critical violations — see the detail below the score card.")
    elif any(score.score is None for score in scores):
        st.caption("Pillars without a scored sample yet show as 0 on this chart.")


def _render_risk_configuration_notice(service: FrameworkService, account_id: int) -> None:
    """Keep the setup-only risk notice visible without duplicating global alerts."""
    notice = next((alert for alert in service.framework_alerts(account_id) if alert.code == "risk_unconfigured"), None)
    if notice is not None:
        st.info(notice.message, icon=":material/info:")


def render_framework_dashboard(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    """Compact monitoring inside the main performance dashboard."""
    service = FrameworkService(repo)
    snapshot = service.risk_snapshot(account.id)
    scores = service.pillar_scores(account.id)
    readiness = service.readiness(account.id)
    _render_framework_focus(repo, account, service, scores, compact=True)
    st.markdown("#### Three-pillar monitor")
    st.caption(tr("Psychology is trader-wide. Risk and System are scoped to {account}.", account=_account_label(account)))
    st.caption(tr("This compact view always uses a fixed 20-trade window. Open Bearings → Monitor to adjust the rolling sample."))
    _render_risk_configuration_notice(service, account.id)
    with st.container(horizontal=True, gap="small"):
        st.metric(tr("Overall readiness"), _score_text(readiness.score), tr(readiness.status.capitalize()), border=True)
        st.metric("Risk state", _state_label(snapshot), border=True)
        st.metric("Today", "—" if snapshot.daily_r is None else format_r(snapshot.daily_r), border=True)
        st.metric("Max drawdown", "—" if snapshot.max_drawdown_percent is None else format_percent(snapshot.max_drawdown_percent), border=True)
    _render_score_cards(scores, account)
    _render_pillar_radar(scores)
    st.caption(tr(readiness.detail))


def render_framework_page(repo: SQLiteJournalRepository) -> None:
    st.markdown('<div class="dashboard-kicker">POST-TRADE JOURNAL</div>', unsafe_allow_html=True)
    st.subheader("Three-pillar framework")
    st.caption("Use completed MT5 trades to assess execution. Alerts are advisory; this journal never sends, blocks, or changes MT5 orders.")
    account = repo.get_active_mt5_account()
    if account is None:
        st.info("Add an approved MT5 account in Settings before using the framework.")
        return
    st.caption(tr("Reviewing {account}. Change the active account in Settings → Approved MT5 accounts.", account=_account_label(account)))
    review_tab, monitor_tab, improve_tab = st.tabs(
        ["Review", "Monitor", "Improve"],
        key="framework-tab",
        on_change="rerun",
    )
    if review_tab.open:
        with review_tab:
            _render_post_trade_review(repo, account)
    if monitor_tab.open:
        with monitor_tab:
            _render_monitor(repo, account)
    if improve_tab.open:
        with improve_tab:
            _render_roadmap(repo, account)


def _render_post_trade_review(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    st.markdown("#### Closed-trade reviews")
    trades = repo.list_closed_trades_for_review(account.id)
    if not trades:
        st.info("No completed MT5 positions have been imported for this account yet.")
        return
    profiles = repo.list_strategy_profiles()
    if not profiles:
        st.warning("Create or select a strategy in Settings → Strategies before saving a full three-pillar review.", icon=":material/info:")
    score_items = service.trade_process_scores(account.id)
    snapshot = service.risk_snapshot(account.id)
    st.caption(f"{tr('Risk state:')} {_state_label(snapshot)} · {tr(snapshot.message)}")
    recent_period_reviews = repo.list_framework_period_reviews(account.id)
    if recent_period_reviews:
        latest_period_review = recent_period_reviews[0]
        st.info(
            tr(
                "Focus from your last {cadence} ({period_end}): {action}",
                cadence=tr(f"{latest_period_review.cadence.capitalize()} review"),
                period_end=latest_period_review.period_end,
                action=latest_period_review.priority_action,
            ),
            icon=":material/flag:",
        )
    trade_scores = {item.trade_id: item for item in score_items}
    _render_review_register(repo, account, trades, trade_scores, profiles)


def _clear_review_dialog() -> None:
    st.session_state.pop("post-trade-review-trade-id", None)
    st.session_state.pop("post-trade-review-queue", None)


def _advance_review_queue(queue: Sequence[int]) -> tuple[int | None, tuple[int, ...]]:
    """Pop the next trade id off a review queue, leaving the remainder."""

    if not queue:
        return None, ()
    return queue[0], tuple(queue[1:])


def _clear_group_dialog() -> None:
    st.session_state.pop("logical-trade-group-editor", None)
    st.session_state.pop("logical-trade-regroup-confirmation", None)


def _bulk_quick_review_key(account_id: int) -> str:
    return f"bulk-quick-review-confirmation-{account_id}"


def _clear_bulk_quick_review(account_id: int) -> None:
    st.session_state.pop(_bulk_quick_review_key(account_id), None)


def _dismiss_bulk_quick_review() -> None:
    """Dismissal has no account argument, so clear the one pending dialog state."""
    for key in [key for key in st.session_state if key.startswith("bulk-quick-review-confirmation-")]:
        st.session_state.pop(key, None)


def _logical_trade_selection_prefix(account_id: int) -> str:
    return f"logical-trade-select-{account_id}-"


def _logical_trade_selection_store_key(account_id: int) -> str:
    return f"logical-trade-selection-{account_id}"


def _logical_trade_page_key(account_id: int) -> str:
    return f"logical-trade-page-{account_id}"


def _clear_logical_trade_selection(account_id: int) -> None:
    """Clear singleton-selection widgets before their next render."""
    prefix = _logical_trade_selection_prefix(account_id)
    st.session_state.pop(_logical_trade_selection_store_key(account_id), None)
    for key in [key for key in st.session_state if key.startswith(prefix)]:
        st.session_state.pop(key, None)


def _defer_logical_trade_selection_reset(account_id: int) -> None:
    """Avoid changing a checkbox after Streamlit has instantiated it."""
    st.session_state[f"logical-trade-selection-reset-{account_id}"] = True
    st.session_state[f"logical-trade-page-reset-{account_id}"] = True


def _prepare_logical_trade_register_state(account_id: int) -> None:
    """Apply deferred card-list cleanup before its widgets are created."""
    if st.session_state.pop(f"logical-trade-selection-reset-{account_id}", False):
        _clear_logical_trade_selection(account_id)
    if st.session_state.pop(f"logical-trade-page-reset-{account_id}", False):
        st.session_state[_logical_trade_page_key(account_id)] = 1


def _toggle_logical_trade_selection(account_id: int, logical_trade_id: int) -> None:
    selected = set(st.session_state.get(_logical_trade_selection_store_key(account_id), ()))
    checkbox_key = f"{_logical_trade_selection_prefix(account_id)}{logical_trade_id}"
    if st.session_state.get(checkbox_key, False):
        selected.add(logical_trade_id)
    else:
        selected.discard(logical_trade_id)
    st.session_state[_logical_trade_selection_store_key(account_id)] = tuple(sorted(selected))


def _change_logical_trade_page(account_id: int, change: int, page_count: int) -> None:
    current = int(st.session_state.get(_logical_trade_page_key(account_id), 1))
    st.session_state[_logical_trade_page_key(account_id)] = max(1, min(page_count, current + change))


@st.dialog("Quick review selected trades", width="large", on_dismiss=_dismiss_bulk_quick_review)
def _render_bulk_quick_review_dialog(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore]) -> None:  # type: ignore[no-untyped-def]
    confirmation = st.session_state.get(_bulk_quick_review_key(account.id))
    if confirmation is None or confirmation.get("account_id") != account.id:
        return
    selected_ids = set(confirmation.get("trade_ids", ()))
    selected = [(trade, scores[trade.id]) for trade in trades if trade.id in selected_ids]
    eligible = [(trade, score) for trade, score in selected if score.review_kind in {"auto_review", "needs_approval"}]
    if not eligible:
        st.info("None of the selected trades still has automatic evidence available for Quick Review.")
        if st.button("Close", key=f"close-bulk-quick-review-{account.id}"):
            _clear_bulk_quick_review(account.id)
            st.rerun()
        return
    labels = {"within_policy": "Within policy", "over_policy": "Over policy", "unavailable": "Unavailable"}
    counts = Counter(score.risk_policy_state for _, score in eligible)
    st.write(f"Accept automatic risk evidence for **{len(eligible)} selected logical trade(s)**?")
    st.caption(" · ".join(f"{labels.get(state, state)}: {count}" for state, count in sorted(counts.items())))
    st.dataframe(
        pd.DataFrame(
            [{
                "Logical trade": f"LT-{trade.id}", "Trade": trade.display_label,
                "Direction": tr(direction_tag(trade.direction).label), "Outcome": tr(outcome_tag(trade.net_pnl).label),
                "Policy evidence": labels.get(score.risk_policy_state, score.risk_policy_state),
            } for trade, score in eligible]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Quick Review saves the displayed automatic evidence as approved review evidence. A Manual Review can still replace it later.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        cancel = st.button("Cancel", key=f"cancel-bulk-quick-review-{account.id}")
        confirm = st.button(f"Quick review {len(eligible)} selected", key=f"confirm-bulk-quick-review-{account.id}", type="primary", icon=":material/done_all:")
    if cancel:
        _clear_bulk_quick_review(account.id)
        st.rerun()
    if not confirm:
        return
    active_policy = repo.get_active_risk_policy(account.id)
    approved_count = 0
    skipped_count = 0
    with st.spinner(tr("Saving…")):
        for trade, score in eligible:
            try:
                repo.approve_auto_review(
                    account_id=account.id, trade_id=trade.id, risk_policy_id=active_policy.id if active_policy else None,
                    risk_evidence_source=score.risk_evidence_source, risk_policy_state=score.risk_policy_state,
                    actual_risk_amount=score.actual_risk_amount,
                    criterion_grades=FrameworkService._automatic_review_grades(score.risk_policy_state),
                )
                approved_count += 1
            except ValueError:
                skipped_count += 1
    message = tr("Quick-reviewed {count} selected trade(s).", count=approved_count)
    if skipped_count:
        message += " " + tr("{count} could not be approved and were skipped.", count=skipped_count)
    st.session_state["post-trade-review-notice"] = message
    _clear_bulk_quick_review(account.id)
    _defer_logical_trade_selection_reset(account.id)
    st.toast(message)
    st.rerun()


def _begin_logical_trade_disband(repo: SQLiteJournalRepository, account: AccountListItem, trade_id: int) -> None:
    try:
        preview = repo.preview_logical_trade_disband(
            account_id=account.id,
            logical_trade_id=trade_id,
        )
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["logical-trade-group-editor"] = {
        "account_id": account.id,
        "logical_trade_id": trade_id,
    }
    st.session_state["logical-trade-regroup-confirmation"] = {
        "account_id": account.id,
        "logical_trade_id": trade_id,
        "position_trade_ids": (),
        "display_label": "",
        "mode": "disband",
        "affected_assessment_count": preview.affected_assessment_count,
        "affected_assessment_labels": preview.affected_assessment_labels,
    }
    st.rerun()


def _render_imported_execution(repo: SQLiteJournalRepository, account: AccountListItem, trade, score: TradeProcessScore) -> None:  # type: ignore[no-untyped-def]
    direction = direction_tag(trade.direction)
    outcome = outcome_tag(trade.net_pnl)
    with st.container(horizontal=True, gap="small"):
        st.badge(tr(direction.label), color=direction.color, icon=direction.icon)
        st.badge(tr(outcome.label), color=outcome.color, icon=outcome.icon)
    with st.container(horizontal=True, gap="small"):
        st.metric("Symbol", trade.symbol, border=True)
        st.metric("Positions", str(trade.position_count), border=True)
        st.metric(f"P&L ({account.account_currency})", format_currency(trade.net_pnl, account.account_currency), border=True)
        st.metric("Trade score", _score_text(score.overall_score), border=True)
        st.metric("Risk evidence", _auto_risk_label(score), border=True)
    st.caption(tr("{trade} · Entry {entry} · Exit {exit}. MT5 execution data is read-only.", trade=trade.display_label, entry=_reporting_time(repo, trade.entry_time, trade.server_utc_offset_minutes), exit=_reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes)))
    st.caption(tr(_risk_evidence_detail(score)))
    if trade.is_group:
        with st.expander(tr("Member positions ({count})", count=trade.position_count)):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            tr("Position"): f"#{member.position_id or '—'}",
                            tr("Opened"): _reporting_time(repo, member.entry_time, member.server_utc_offset_minutes),
                            tr("Closed"): _reporting_time(repo, member.exit_time, member.server_utc_offset_minutes),
                            f"P&L ({account.account_currency})": format_currency(member.net_pnl, account.account_currency),
                        }
                        for member in trade.members
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _grade_control(label: str, *, existing: str | None, key: str) -> str | None:
    # Only pass a default on this widget's first render. Once a value is stored under `key`
    # (e.g. by a "Mark as Pass" button's on_click), passing a non-None default alongside it is
    # ambiguous to Streamlit and logs a "default value but also set via Session State" warning.
    default = existing.capitalize() if existing and key not in st.session_state else None
    choice = st.segmented_control(label, GRADE_OPTIONS, format_func=tr, default=default, key=key, width="content")
    return None if choice is None else choice.casefold()


def _default_policy_adherence_grade(risk_policy_state: str) -> str | None:
    """Default Risk policy adherence to already-computed automatic risk evidence, for a fresh review only."""
    return {"within_policy": "pass", "over_policy": "fail"}.get(risk_policy_state)


def _set_pillar_grades_to_pass(
    trade_id: int,
    criteria: Sequence[str],
    state: MutableMapping[str, object] | None = None,
) -> None:
    """Apply an explicit Pass-all choice to one pillar's stable widget keys."""

    target = st.session_state if state is None else state
    for criterion in criteria:
        target[f"assessment-{trade_id}-{criterion}"] = "Pass"


def _grade_summary(trade_id: int, criteria: Sequence[str], existing: dict[str, str] | None) -> tuple[int, int]:
    """Return completed grades and non-pass exceptions for the review header."""

    values = [st.session_state.get(f"assessment-{trade_id}-{criterion}", (existing or {}).get(criterion, "").capitalize()) for criterion in criteria]
    completed = sum(value in GRADE_OPTIONS for value in values)
    exceptions = sum(value in {"Partial", "Fail"} for value in values)
    return completed, exceptions


@st.dialog("Post-trade assessment", width="large", on_dismiss=_clear_review_dialog)
def _render_post_trade_review_dialog(repo: SQLiteJournalRepository, account: AccountListItem, trade, score: TradeProcessScore, profiles) -> None:  # type: ignore[no-untyped-def]
    existing = repo.get_post_trade_assessment_for_trade(trade.id)
    # A prior "auto" row is not a human review — only a "manual" row is safe to pre-fill
    # from (correction). An auto row's own defaults are neutral placeholders, not judgments;
    # only its evidence-backed policy_adherence value is reused, further below.
    existing_manual = existing if existing is not None and existing.method == "manual" else None
    queue = tuple(st.session_state.get("post-trade-review-queue", ()))
    st.caption(tr("Correct {trade}" if existing_manual else "Review {trade}", trade=trade.display_label))
    _render_imported_execution(repo, account, trade, score)
    if monitoring_detail := _automatic_risk_monitoring_detail(score):
        st.warning(
            f"{monitoring_detail} Select ‘Traded after hard shutdown’ only when your review confirms it breached your rule.",
            icon=":material/warning:",
        )
    if failure_detail := _process_failure_detail(score):
        st.error(
            f"{failure_detail}. This hard block overrides the numeric pillar scores and classifies a profitable trade as a Bad Win.",
            icon=":material/error:",
        )
    if st.button("Manage positions", key=f"manage-logical-trade-{trade.id}", icon=":material/group_work:"):
        _clear_review_dialog()
        st.session_state["logical-trade-group-editor"] = {"account_id": account.id, "logical_trade_id": trade.id}
        st.rerun()
    if existing is not None:
        st.caption("Changing member positions supersedes this assessment and requires a new review. Changing only the label keeps it active.")
    if not profiles:
        st.warning("A Strategy profile is required for a full post-trade assessment.")
        return
    policy = repo.get_active_risk_policy(account.id)
    rule_settings = repo.get_framework_rule_settings()
    enabled_hard_rules = {
        code
        for code, active in {
            "oversized_revenge": rule_settings.oversized_revenge_hard,
            "mandatory_setup_absent": rule_settings.mandatory_setup_hard,
            "stop_widened": rule_settings.stop_widened_hard,
            "shutdown_breach": rule_settings.shutdown_breach_hard,
        }.items()
        if active
    }
    existing_hard_rules = set(existing.hard_rule_codes) if existing else set()
    available_hard_rules = [
        code for code in HARD_RULE_LABELS if code in enabled_hard_rules or code in existing_hard_rules
    ]
    default_id = existing_manual.strategy_profile_id if existing_manual else score.mapped_strategy.id if score.mapped_strategy else _default_strategy_id(repo)
    strategy_index = next((index for index, item in enumerate(profiles) if item.id == default_id), 0)
    st.markdown("##### Assessment")
    st.caption("\\* Required")
    with st.form(f"post-trade-assessment-{trade.id}"):
        strategy = st.selectbox(f"{tr('Strategy')} *", profiles, index=strategy_index, format_func=lambda item: item.name)
        setup_options = [None, *repo.list_strategy_setups(strategy.id)]
        session_options = [None, *repo.list_review_context_tags("session")]
        regime_options = [None, *repo.list_review_context_tags("regime")]
        context_left, context_middle, context_right = st.columns(3)
        selected_setup = context_left.selectbox(
            "Setup (optional)", setup_options, format_func=lambda item: "Unspecified" if item is None else item.name,
            key=f"assessment-{trade.id}-setup",
        )
        selected_session = context_middle.selectbox(
            "Session (optional)", session_options, format_func=lambda item: "Unspecified" if item is None else item.name,
            key=f"assessment-{trade.id}-session",
        )
        selected_regime = context_right.selectbox(
            "Market regime (optional)", regime_options, format_func=lambda item: "Unspecified" if item is None else item.name,
            key=f"assessment-{trade.id}-regime",
        )
        pillars = (
            ("Psychology", PSYCHOLOGY_CRITERIA),
            ("Risk management", RISK_CRITERIA),
            ("Trading system", SYSTEM_CRITERIA),
        )
        summaries = [_grade_summary(trade.id, criteria, existing_manual.criterion_grades if existing_manual else None) for _, criteria in pillars]
        completed = sum(item[0] for item in summaries)
        exceptions = sum(item[1] for item in summaries)
        st.caption(
            tr(
                "{completed} of {total} criteria graded · {exceptions} exception{plural}",
                completed=completed,
                total=sum(len(criteria) for _, criteria in pillars),
                exceptions=exceptions,
                plural="s" if exceptions != 1 else "",
            )
        )
        st.caption("Mark a pillar as Pass, then change only the Partial or Fail exceptions.")
        st.form_submit_button(
            "Mark all criteria as Pass",
            key=f"assessment-{trade.id}-pass-all",
            icon=":material/done_all:",
            on_click=_set_pillar_grades_to_pass,
            args=(trade.id, ASSESSMENT_CRITERIA),
        )
        grades: dict[str, str | None] = {}
        for (title, criteria), (done, _) in zip(pillars, summaries, strict=True):
            pillar_section = st.container(border=True)
            pillar_section.markdown(f"###### :{PILLAR_ACCENT_COLORS[title]}[{tr(title)}] · {done}/{len(criteria)}")
            pillar_section.form_submit_button(
                tr("Mark {pillar} as Pass", pillar=tr(title)),
                key=f"assessment-{trade.id}-{title}-pass-all",
                icon=":material/done_all:",
                on_click=_set_pillar_grades_to_pass,
                args=(trade.id, criteria),
            )
            for row_start in range(0, len(criteria), CRITERIA_GRID_COLUMNS):
                row_criteria = criteria[row_start : row_start + CRITERIA_GRID_COLUMNS]
                for criterion_column, criterion in zip(pillar_section.columns(CRITERIA_GRID_COLUMNS), row_criteria, strict=False):
                    criterion_existing = existing_manual.criterion_grades.get(criterion) if existing_manual else None
                    if criterion_existing is None and criterion == "policy_adherence":
                        criterion_existing = _default_policy_adherence_grade(score.risk_policy_state)
                    with criterion_column:
                        grades[criterion] = _grade_control(
                            f"{tr(CRITERION_LABELS[criterion])} *",
                            existing=criterion_existing,
                            key=f"assessment-{trade.id}-{criterion}",
                        )
        actual_risk = st.text_input(
            "Actual risk amount (optional)",
            value=existing_manual.declared_actual_risk_amount if existing_manual and existing_manual.declared_actual_risk_amount else "",
            placeholder="Enter a verified amount when automatic evidence is not sufficient",
            help="Overrides automatic evidence for this logical trade's policy comparison. It does not rewrite immutable MT5 position history or account-limit monitoring.",
        )
        if policy is not None:
            st.caption(
                tr(
                    "Risk policy v{version}: Standard 1R {standard}% · maximum {maximum}%.",
                    version=policy.version,
                    standard=policy.standard_risk_per_trade_percent,
                    maximum=policy.maximum_risk_per_trade_percent,
                )
            )
        else:
            st.caption("No active Risk policy is attached; the assessment still records your judgement, while automatic limit checks remain unavailable.")
        violation_codes = st.multiselect(
            "Reason tags",
            options=list(VIOLATION_LABELS),
            default=list(existing_manual.violation_codes) if existing_manual else [],
            format_func=lambda code: tr(VIOLATION_LABELS[code]),
            help="Tag the cause of a partial or failed assessment so period reviews can identify recurring issues.",
        )
        hard_rules = st.multiselect(
            "Hard-rule events",
            options=available_hard_rules,
            default=list(existing_manual.hard_rule_codes) if existing_manual else [],
            format_func=lambda code: tr(HARD_RULE_LABELS[code]),
            help="Enabled events selected on save set Hard-rule status to Fail. That result is snapshotted for this assessment, so later Review rules changes do not rewrite it. Automatic Risk limits are monitoring evidence, not hard failures by themselves.",
        )
        if not available_hard_rules:
            st.caption("No hard-rule events are enabled. Enable one in Settings → Review rules to record it on a new assessment.")
        st.markdown("##### Review details")
        note = st.text_area(f"{tr('What happened and what did you learn?')} *", value=existing_manual.post_review_note if existing_manual else "", placeholder="Describe execution independently of P&L.")
        action = st.text_area("Corrective action", value=existing_manual.corrective_action if existing_manual and existing_manual.corrective_action else "", placeholder="Required when any criterion is Partial or Fail, or a hard rule is selected.")
        submitted = st.form_submit_button("Update assessment" if existing_manual else "Save assessment", type="primary")
        submit_and_next = (
            st.form_submit_button(tr("Save & review next ({count} left)", count=len(queue)), icon=":material/skip_next:")
            if queue
            else False
        )
    if not (submitted or submit_and_next):
        _render_review_history(repo, account.id, trade)
        return
    if any(value is None for value in grades.values()):
        st.error("Rate every criterion as Pass, Partial, or Fail before saving.")
        return
    try:
        with st.spinner(tr("Saving…")):
            repo.save_post_trade_assessment(
                account_id=account.id,
                trade_id=trade.id,
                risk_policy_id=policy.id if policy else None,
                strategy_profile_id=strategy.id,
                criterion_grades={key: value for key, value in grades.items() if value is not None},
                violation_codes=tuple(violation_codes),
                hard_rule_codes=tuple(hard_rules),
                declared_actual_risk_amount=actual_risk,
                post_review_note=note,
                corrective_action=action,
                review_context=ReviewContextSelection(
                    strategy_setup_id=None if selected_setup is None else selected_setup.id,
                    session_tag_id=None if selected_session is None else selected_session.id,
                    regime_tag_id=None if selected_regime is None else selected_regime.id,
                ),
            )
    except ValueError as error:
        st.error(str(error))
    else:
        st.session_state["post-trade-review-notice"] = "Post-trade assessment saved."
        st.toast(tr("Post-trade assessment saved."))
        if submit_and_next:
            next_id, remaining = _advance_review_queue(queue)
            st.session_state["post-trade-review-trade-id"] = next_id
            st.session_state["post-trade-review-queue"] = remaining
        else:
            _clear_review_dialog()
        st.rerun()


def _render_review_history(repo: SQLiteJournalRepository, account_id: int, trade) -> None:  # type: ignore[no-untyped-def]
    revisions = repo.list_post_trade_assessment_revisions(trade.id)
    superseded = repo.list_superseded_post_trade_assessments_for_trade(
        account_id=account_id,
        logical_trade_id=trade.id,
    )
    if not revisions and not superseded:
        return
    with st.expander(f"Assessment history ({len(revisions) + len(superseded)})"):
        for revision in revisions:
            failed = sum(value == "fail" for value in revision.criterion_grades.values())
            strategy_label = revision.strategy_snapshot.name if revision.strategy_snapshot is not None else tr("Auto-approved")
            st.markdown(f"**{tr('Version {version}', version=revision.version)}** · {revision.archived_at[:19]} · {strategy_label}")
            hard_rule_text = ", ".join(tr(HARD_RULE_LABELS.get(code, code)) for code in revision.hard_rule_codes) or tr("none")
            st.caption(tr("{failed} failed criterion/criteria · Hard rules: {hard_rules}", failed=failed, hard_rules=hard_rule_text))
            if revision.post_review_note:
                st.write(revision.post_review_note)
        for assessment in superseded:
            positions = ", ".join(f"#{position_id}" for position_id in assessment.assessed_position_ids)
            st.markdown(f"**{tr('Superseded assessment')}** · {assessment.superseded_at[:19] if assessment.superseded_at else '—'} · {assessment.assessed_trade_label}")
            reason = tr(assessment.superseded_reason) if assessment.superseded_reason else tr("Logical-trade membership changed")
            st.caption(tr("Assessed {positions} · {reason}", positions=positions, reason=reason))
            if assessment.post_review_note:
                st.write(assessment.post_review_note)


def _default_strategy_id(repo: SQLiteJournalRepository) -> int | None:
    try:
        return repo.get_journal_settings().default_strategy_profile_id
    except RuntimeError:
        return None


@st.dialog("Manage logical-trade positions", width="large", on_dismiss=_clear_group_dialog)
def _render_logical_trade_group_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    existing_group=None,
    selected_position_trade_ids: tuple[int, ...] = (),
) -> None:  # type: ignore[no-untyped-def]
    """Create or regroup a logical trade; imported MT5 positions stay immutable."""
    existing_members = () if existing_group is None else existing_group.members
    positions = repo.list_imported_positions_for_grouping(account.id)
    units = repo.list_closed_trades_for_review(account.id)
    unit_by_position_id = {
        member.id: unit
        for unit in units
        for member in unit.members
    }
    labels = {
        position.id: (
            f"#{position.position_id or '—'} · {position.symbol} {position.direction} · "
            f"{format_currency(position.net_pnl, account.account_currency)} · "
            f"{unit_by_position_id[position.id].display_label if unit_by_position_id[position.id].is_group else 'Standalone'}"
        )
        for position in positions
    }
    editor_id = None if existing_group is None else existing_group.id
    selected_position_trade_ids = tuple(
        position_id for position_id in selected_position_trade_ids if position_id in labels
    )
    confirmation = st.session_state.get("logical-trade-regroup-confirmation")
    if confirmation is not None and confirmation.get("account_id") != account.id:
        _clear_group_dialog()
        st.rerun()
    if confirmation is not None:
        _render_logical_trade_regroup_confirmation(repo, account, confirmation)
        return
    is_selection_create = existing_group is None and bool(selected_position_trade_ids)
    st.caption(
        "The selected single-position logical trades will be combined into one logical trade."
        if is_selection_create
        else "Every imported position starts as one logical trade. Select the current members for this logical trade; "
        "selected positions may be moved from another group. Members must share account, symbol, direction, and imported Risk-policy version."
    )
    with st.form(f"logical-trade-group-{account.id}-{editor_id}"):
        label = st.text_input(
            "Trade label (optional)",
            value="" if existing_group is None else existing_group.custom_label or "",
            placeholder="e.g. London breakout scale-in",
        )
        selected = st.multiselect(
            "Positions",
            options=list(labels),
            default=[member.id for member in existing_members] if existing_group is not None else list(selected_position_trade_ids),
            format_func=labels.get,
            placeholder="Choose two or more positions",
            disabled=is_selection_create,
        )
        save = st.form_submit_button(
            "Create logical trade" if existing_group is None else "Continue",
            type="primary",
            icon=":material/group_work:",
        )
        disband = (
            st.form_submit_button("Disband into individual trades", type="secondary")
            if existing_group is not None and existing_group.is_group
            else False
        )
    if not save and not disband:
        return
    try:
        if disband:
            preview = repo.preview_logical_trade_disband(
                account_id=account.id,
                logical_trade_id=existing_group.id,
            )
            mode = "disband"
        else:
            preview = repo.preview_logical_trade_regroup(
                account_id=account.id,
                position_trade_ids=tuple(selected),
                logical_trade_id=editor_id,
            )
            mode = "regroup"
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["logical-trade-regroup-confirmation"] = {
        "account_id": account.id,
        "logical_trade_id": editor_id,
        "position_trade_ids": tuple(selected),
        "display_label": label,
        "mode": mode,
        "affected_assessment_count": preview.affected_assessment_count,
        "affected_assessment_labels": preview.affected_assessment_labels,
    }
    st.rerun()


def _render_logical_trade_regroup_confirmation(repo: SQLiteJournalRepository, account: AccountListItem, confirmation: dict) -> None:  # type: ignore[type-arg]
    count = confirmation["affected_assessment_count"]
    if confirmation["mode"] == "disband":
        st.warning("This will split the current logical trade into individual position trades.")
    else:
        st.warning("This will apply the selected current membership to the logical trade.")
    if count:
        st.error(
            tr(
                "{count} saved assessment(s) will be superseded and removed from active scores, reports, alerts, and roadmap evidence. Each resulting logical trade needs a new review.",
                count=count,
            )
        )
        for label in confirmation["affected_assessment_labels"]:
            st.caption(tr("Supersede: {label}", label=label))
    else:
        st.caption("No active saved assessment is affected. Dashboard reporting will recalculate from the new grouping.")
    with st.container(horizontal=True, gap="small"):
        confirm = st.button(
            "Confirm disband" if confirmation["mode"] == "disband" else "Confirm regroup",
            type="primary",
            icon=":material/check:",
        )
        back = st.button("Back", icon=":material/arrow_back:")
    if back:
        st.session_state.pop("logical-trade-regroup-confirmation", None)
        st.rerun()
    if not confirm:
        return
    try:
        with st.spinner(tr("Saving…")):
            if confirmation["mode"] == "disband":
                result = repo.disband_logical_trade_group(
                    account_id=account.id,
                    logical_trade_id=confirmation["logical_trade_id"],
                )
                notice = "Logical trade disbanded into individual positions."
            else:
                result = repo.regroup_logical_trade(
                    account_id=account.id,
                    logical_trade_id=confirmation["logical_trade_id"],
                    position_trade_ids=tuple(confirmation["position_trade_ids"]),
                    display_label=confirmation["display_label"],
                )
                notice = "Logical trade saved."
        if result.superseded_assessment_count:
            notice += f" {result.superseded_assessment_count} assessment(s) now need re-review."
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["post-trade-review-notice"] = notice
    st.toast(tr(notice))
    _defer_logical_trade_selection_reset(account.id)
    _clear_group_dialog()
    st.rerun()


def _render_review_register(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore], profiles) -> None:  # type: ignore[no-untyped-def]
    active_policy = repo.get_active_risk_policy(account.id)
    ordered = sorted(trades, key=lambda item: (item.exit_time, item.id), reverse=True)
    groups = {
        "needs_approval": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].review_kind == "needs_approval"],
        "auto_reviewed": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].review_kind == "auto_review"],
        "manual_reviewed": [
            (trade, scores[trade.id])
            for trade in ordered
            if scores[trade.id].review_kind in {"approved_auto_review", "manual_review"}
        ],
        "all": [(trade, scores[trade.id]) for trade in ordered],
    }
    _prepare_logical_trade_register_state(account.id)
    filter_names = {
        "needs_approval": "Requires review",
        "auto_reviewed": "Auto-reviewed",
        "manual_reviewed": "Reviewed",
        "all": "All",
    }
    filter_labels = {key: f"{tr(filter_names[key])} ({len(items)})" for key, items in groups.items()}
    filter_value = st.segmented_control(
        "Review status", list(groups),
        format_func=filter_labels.get,
        default="needs_approval", required=True, width="content", key=f"review-filter-{account.id}",
    )
    selected_group = filter_value
    failed_only = st.checkbox("Show failed only", key=f"review-failed-only-{account.id}")
    filter_key = f"logical-trade-selection-filter-{account.id}"
    current_filter = (selected_group, failed_only)
    previous_filter = st.session_state.get(filter_key)
    if previous_filter is not None and previous_filter != current_filter:
        _clear_logical_trade_selection(account.id)
        st.session_state[_logical_trade_page_key(account.id)] = 1
    st.session_state[filter_key] = current_filter
    visible = groups[selected_group]
    if failed_only:
        visible = [(trade, score) for trade, score in visible if score.process_status == "FAIL"]
    visible_by_id = {trade.id: trade for trade, _ in visible}
    single_position_by_id = {trade.id: trade for trade, _ in visible if not trade.is_group}
    selected_logical_trade_ids = tuple(
        trade_id
        for trade_id in st.session_state.get(_logical_trade_selection_store_key(account.id), ())
        if trade_id in visible_by_id
    )
    st.session_state[_logical_trade_selection_store_key(account.id)] = selected_logical_trade_ids
    selected_position_trade_ids = tuple(
        single_position_by_id[trade_id].members[0].id for trade_id in selected_logical_trade_ids
        if trade_id in single_position_by_id
    )
    selected_reviewable_count = sum(
        scores[trade_id].review_kind in {"auto_review", "needs_approval"}
        for trade_id in selected_logical_trade_ids
    )
    with st.container(horizontal=True, gap="small"):
        create = st.button(
            f"Create logical trade ({len(selected_position_trade_ids)})",
            key=f"create-logical-trade-{account.id}",
            icon=":material/group_work:",
            type="primary",
            disabled=len(selected_position_trade_ids) < 2,
        )
        st.button(
            "Clear selection",
            key=f"clear-logical-trade-selection-{account.id}",
            icon=":material/clear:",
            disabled=not selected_logical_trade_ids,
            on_click=_clear_logical_trade_selection,
            args=(account.id,),
        )
        bulk_selected = st.button(
            f"Quick review selected ({selected_reviewable_count})",
            key=f"bulk-quick-review-selected-{account.id}",
            icon=":material/done_all:",
            type="primary",
            disabled=selected_reviewable_count == 0,
            help="Review selected Awaiting approval or Requires review trades in one confirmed action.",
        )
        bulk_approve = (
            st.button(
                tr("Approve all visible within-policy ({count})", count=len(visible)),
                key=f"bulk-approve-auto-review-{account.id}",
                icon=":material/done_all:",
                help="Approve every within-policy auto-review trade currently shown by this filter, one click for all of them.",
            )
            if selected_group == "auto_reviewed" and visible
            else False
        )
    if bulk_approve:
        approved_count = 0
        skipped_count = 0
        with st.spinner(tr("Saving…")):
            for trade, score in visible:
                try:
                    repo.approve_auto_review(
                        account_id=account.id, trade_id=trade.id, risk_policy_id=active_policy.id if active_policy else None,
                        risk_evidence_source=score.risk_evidence_source,
                        risk_policy_state=score.risk_policy_state,
                        actual_risk_amount=score.actual_risk_amount,
                        criterion_grades=FrameworkService._automatic_review_grades(score.risk_policy_state),
                    )
                    approved_count += 1
                except ValueError:
                    skipped_count += 1
        message = tr("Approved {count} within-policy trade(s).", count=approved_count)
        if skipped_count:
            message += " " + tr("{count} could not be approved and were skipped.", count=skipped_count)
        st.session_state["post-trade-review-notice"] = message
        st.toast(message)
        st.rerun()
    if bulk_selected:
        st.session_state[_bulk_quick_review_key(account.id)] = {
            "account_id": account.id,
            "trade_ids": selected_logical_trade_ids,
        }
        st.rerun()
    if create:
        st.session_state["logical-trade-group-editor"] = {
            "account_id": account.id,
            "logical_trade_id": None,
            "selected_position_trade_ids": selected_position_trade_ids,
        }
        st.rerun()
    page_count = max(1, (len(visible) + REVIEW_PAGE_SIZE - 1) // REVIEW_PAGE_SIZE)
    page_key = _logical_trade_page_key(account.id)
    current_page = int(st.session_state.get(page_key, 1))
    current_page = max(1, min(page_count, current_page))
    st.session_state[page_key] = current_page
    with st.container(horizontal=True, gap="small"):
        st.button(
            "Previous",
            key=f"previous-logical-trade-page-{account.id}",
            icon=":material/chevron_left:",
            disabled=current_page == 1,
            on_click=_change_logical_trade_page,
            args=(account.id, -1, page_count),
        )
        st.caption(f"Page {current_page} of {page_count} · {len(visible)} logical trade{'s' if len(visible) != 1 else ''}")
        st.button(
            "Next",
            key=f"next-logical-trade-page-{account.id}",
            icon=":material/chevron_right:",
            disabled=current_page == page_count,
            on_click=_change_logical_trade_page,
            args=(account.id, 1, page_count),
        )
    if not visible:
        status_text = tr(filter_names[selected_group]).casefold()
        if failed_only:
            st.info(tr("No {status} ({qualifier}) trades for this account.", status=status_text, qualifier=tr("failed")))
        else:
            st.info(tr("No {status} trades for this account.", status=status_text))
    else:
        st.caption("P = Psychology · R = Risk management · S = Trading system. This is each trade's own 13-criterion score — the Monitor tab's rolling pillar scores use a different calculation and can show a different number.")
        st.caption("Classification below: first word = process quality (Good/Needs improvement/Bad), second word = P&L outcome (Win/Loss/Breakeven) — independent of each other.")
        position_by_id = {trade.id: index for index, (trade, _) in enumerate(visible)}
        start = (current_page - 1) * REVIEW_PAGE_SIZE
        page_items = visible[start : start + REVIEW_PAGE_SIZE]
        header = st.columns([0.5, 0.85, 1.35, 1.3, 0.75, 1.1, 0.85, 0.75, 1.0])
        for column, label in zip(
            header,
            ("Select", "Logical trade", "Trade", "Positions", "P&L", "Review", "Score", "Hard rules", "Actions"),
            strict=True,
        ):
            column.caption(tr(label))
        for trade, score in page_items:
            review = {
                "needs_approval": "Requires review",
                "auto_review": "Awaiting approval",
                "approved_auto_review": "Auto",
                "manual_review": "Manual",
            }.get(score.review_kind, "Requires review")
            with st.container(border=True):
                select_column, logical_column, trade_column, positions_column, pnl_column, review_column, score_column, process_column, actions_column = st.columns(
                    [0.5, 0.85, 1.35, 1.3, 0.75, 1.1, 0.85, 0.75, 1.0]
                )
                checkbox_key = f"{_logical_trade_selection_prefix(account.id)}{trade.id}"
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = trade.id in selected_logical_trade_ids
                select_column.checkbox(
                    f"Select LT-{trade.id}",
                    key=checkbox_key,
                    label_visibility="collapsed",
                    help=(
                        "Select this logical trade for Bulk Quick Review. Grouped trades cannot be used to create another logical trade."
                        if trade.is_group
                        else "Select this logical trade for Bulk Quick Review or to group it with other single-position trades."
                    ),
                    on_change=_toggle_logical_trade_selection,
                    args=(account.id, trade.id),
                )
                logical_column.markdown(f"**LT-{trade.id}**")
                trade_column.write(trade.display_label)
                direction = direction_tag(trade.direction)
                outcome = outcome_tag(trade.net_pnl)
                trade_column.badge(tr(direction.label), color=direction.color, icon=direction.icon)
                positions_column.write(", ".join(f"#{position_id}" for position_id in trade.position_ids))
                pnl_column.write(format_currency(trade.net_pnl, account.account_currency))
                pnl_column.badge(tr(outcome.label), color=outcome.color, icon=outcome.icon)
                review_column.write(tr(review))
                score_column.markdown(f"**{_score_text(score.overall_score)}**")
                psychology_flag = " ⚠" if score.psychology_hard_block else ""
                risk_flag = " ⚠" if score.risk_hard_block else ""
                system_flag = " ⚠" if score.system_hard_block else ""
                score_column.caption(
                    f"P {_score_text(score.psychology_score)}{psychology_flag}  \n"
                    f"R {_score_text(score.risk_score)}{risk_flag}  \n"
                    f"S {_score_text(score.system_score)}{system_flag}"
                )
                if score.process_status == "FAIL":
                    process_column.badge(tr("Fail"), icon=":material/error:", color="red")
                elif score.process_status == "PASS":
                    process_column.badge(tr("Clear"), icon=":material/check:", color="green")
                else:
                    process_column.write("—")
                if actions_column.button(
                    "Review",
                    key=f"open-logical-trade-review-{account.id}-{trade.id}",
                    type="tertiary",
                    icon=":material/edit:",
                ):
                    st.session_state["post-trade-review-trade-id"] = trade.id
                    st.session_state["post-trade-review-queue"] = tuple(
                        item.id for item, _ in visible[position_by_id[trade.id] + 1 :]
                    )
                    st.rerun()
                if score.review_kind in {"needs_approval", "auto_review"}:
                    is_within_policy = score.review_kind == "auto_review"
                    label = "Approve" if is_within_policy else "Quick review"
                    help_text = (
                        "Approve this within-policy automatic risk evidence so it counts toward your pillar scores."
                        if is_within_policy
                        else "Accept the automatic risk evidence in one click instead of a full 13-criterion review."
                    )
                    if actions_column.button(
                        label,
                        key=f"approve-auto-review-{account.id}-{trade.id}",
                        type="tertiary",
                        icon=":material/check:",
                        help=help_text,
                    ):
                        try:
                            with st.spinner(tr("Saving…")):
                                repo.approve_auto_review(
                                    account_id=account.id, trade_id=trade.id, risk_policy_id=active_policy.id if active_policy else None,
                                    risk_evidence_source=score.risk_evidence_source,
                                    risk_policy_state=score.risk_policy_state,
                                    actual_risk_amount=score.actual_risk_amount,
                                    criterion_grades=FrameworkService._automatic_review_grades(score.risk_policy_state),
                                )
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.session_state["post-trade-review-notice"] = "Automatic risk evidence approved."
                            st.toast(tr("Automatic risk evidence approved."))
                            st.rerun()
                if trade.is_group and actions_column.button(
                    "Ungroup",
                    key=f"ungroup-logical-trade-{account.id}-{trade.id}",
                    type="tertiary",
                    icon=":material/group_off:",
                ):
                    _begin_logical_trade_disband(repo, account, trade.id)
                summary = (
                    f"{trade.symbol} {trade.direction} · Closed {_reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes)} "
                    f"· {_auto_risk_label(score)} · {tr(score.classification or 'Unclassified')}"
                )
                st.caption(summary)
                if failure_detail := _process_failure_detail(score):
                    st.caption(failure_detail)
                if monitoring_detail := _automatic_risk_monitoring_detail(score):
                    st.caption(monitoring_detail)
        st.caption("Automatic risk evidence only counts toward scores once approved here in one click, or replaced by a full assessment.")
    group_editor = st.session_state.get("logical-trade-group-editor")
    if group_editor is not None and group_editor.get("account_id") == account.id:
        group_id = group_editor.get("logical_trade_id")
        group = next((item for item in trades if item.id == group_id), None) if group_id is not None else None
        if group_id is None or group is not None:
            _render_logical_trade_group_dialog(
                repo,
                account,
                group,
                tuple(group_editor.get("selected_position_trade_ids", ())),
            )
        else:
            _clear_group_dialog()
    if st.session_state.get(_bulk_quick_review_key(account.id)) is not None:
        _render_bulk_quick_review_dialog(repo, account, trades, scores)
    selected = st.session_state.get("post-trade-review-trade-id")
    if selected is None:
        return
    skipped_stale = False
    item = next(((trade, score) for trade, score in [(trade, scores[trade.id]) for trade in ordered] if trade.id == selected), None)
    while item is None and selected is not None:
        # The queued trade's logical-trade membership changed (regroup/disband) mid-session.
        # Skip past it instead of silently discarding the rest of the review queue.
        skipped_stale = True
        queue = tuple(st.session_state.get("post-trade-review-queue", ()))
        selected, remaining = _advance_review_queue(queue)
        st.session_state["post-trade-review-trade-id"] = selected
        st.session_state["post-trade-review-queue"] = remaining
        if selected is not None:
            item = next(((trade, score) for trade, score in [(trade, scores[trade.id]) for trade in ordered] if trade.id == selected), None)
    if skipped_stale:
        st.toast(tr("A queued trade could no longer be reviewed (its logical trade changed) and was skipped."))
    if item is None:
        _clear_review_dialog()
        return
    _render_post_trade_review_dialog(repo, account, item[0], item[1], profiles)


def _render_monitor(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    st.markdown("#### Monitoring")
    st.caption(tr("Psychology is trader-wide. Risk and System are scoped to {account}.", account=_account_label(account)))
    _render_risk_configuration_notice(service, account.id)
    controls, scope_note = st.columns((2, 3))
    with controls:
        window = st.slider(tr("Rolling sample"), min_value=10, max_value=100, value=20, step=5, key=f"framework-window-{account.id}")
        period = st.segmented_control("Analysis period", ["This month", "Last 90 days", "All time", "Custom"], default="Last 90 days", required=True, key=f"framework-analysis-period-{account.id}")
    today = date.today()
    if period == "This month":
        start_date, end_date = today.replace(day=1), today
    elif period == "Last 90 days":
        start_date, end_date = today - timedelta(days=89), today
    elif period == "All time":
        dates = [service._trade_date(item.exit_time, item.server_utc_offset_minutes) for item in service.trade_process_scores(account.id)]
        start_date, end_date = min(dates, default=today), today
    else:
        with scope_note:
            range_value = st.date_input("Analysis date range", value=(today - timedelta(days=89), today), key=f"framework-analysis-dates-{account.id}")
        if not isinstance(range_value, tuple) or len(range_value) != 2:
            st.info("Choose a start and end date for Monitor analysis.")
            return
        start_date, end_date = range_value
    with scope_note:
        st.caption(f"Analysis: {start_date.isoformat()} to {end_date.isoformat()} · scores and gates always use the rolling reviewed sample.")
    critical_threshold = repo.get_framework_rule_settings().repeated_critical_threshold
    st.caption(
        tr(
            "Caution triggers after {threshold} repeated critical violations within this window — a smaller window reaches that count sooner.",
            threshold=critical_threshold,
        )
    )
    scores = service.pillar_scores(account.id, window=int(window))
    readiness = service.readiness(account.id, window=int(window))
    coverage = service.risk_evidence_coverage(account.id, window=int(window))
    analysis = service.monitor_analysis(account.id, start_date=start_date, end_date=end_date, window=int(window))
    _render_framework_focus(repo, account, service, scores)
    st.metric(tr("Overall readiness"), _score_text(readiness.score), tr(readiness.status.capitalize()), border=True)
    st.caption(readiness.detail)
    with st.container(horizontal=True, gap="small"):
        st.metric("Risk checks", f"{coverage.approved}/{coverage.total}", "approved evidence", border=True)
        st.metric("Risk pending", str(coverage.pending), border=True)
        st.metric("Over policy", str(coverage.over_policy), border=True)
        st.metric("Risk unavailable", str(coverage.unavailable), border=True)
    st.caption("Approved Quick Risk Checks and Manual Reviews both feed pillar scores, readiness, and roadmap gates.")
    _render_score_cards(scores, account)
    _render_pillar_radar(scores)
    component_rows = [
        {tr("Pillar"): tr(PILLAR_NAMES[score.pillar]), tr("Metric"): tr(name), tr("Score"): _score_text(value), tr("Scope"): tr(score.scope)}
        for score in scores for name, value in score.component_scores
    ]
    if component_rows:
        st.markdown("##### What drives the current scores")
        st.dataframe(pd.DataFrame(component_rows), hide_index=True, width="stretch")
        present_names = {name for score in scores for name, _ in score.component_scores}
        with st.expander(tr("What do these mean?")):
            for name in COMPONENT_DEFINITIONS:
                if name in present_names:
                    st.caption(f"**{tr(name)}** — {tr(COMPONENT_DEFINITIONS[name])}")
    _render_monitor_insights(analysis)
    process_tab, risk_tab, system_tab = st.tabs(["Process & outcomes", "Risk", "System & context"])
    with process_tab:
        _render_monitor_process(service, account, analysis, int(window))
    with risk_tab:
        _render_monitor_risk(analysis, service.risk_snapshot(account.id))
    with system_tab:
        _render_monitor_system(analysis)
    _render_period_reviews(repo, account, service)


def _render_monitor_insights(analysis: MonitorAnalysisReport) -> None:
    st.markdown("##### Evidence-led actions")
    if not analysis.insights:
        st.success("No urgent Monitor finding in the selected evidence. Keep collecting reviewed trades and complete the active focus.")
        return
    for insight in analysis.insights:
        getattr(st, {"critical": "error", "warning": "warning"}.get(insight.severity, "info"))(insight.message, icon=":material/analytics:")


def _render_monitor_process(service: FrameworkService, account: AccountListItem, analysis: MonitorAnalysisReport, window: int) -> None:
    trend = [row for row in service.rolling_score_trend(account.id, window=window) if analysis.start_date <= row[0][:10] <= analysis.end_date]
    if trend:
        frame = pd.DataFrame(trend, columns=["Closed", "Psychology", "Risk management", "Trading system"]).set_index("Closed")
        st.line_chart(frame, width="stretch")
        st.caption("Each point keeps the same approved-review scoring rules as the score cards. Psychology is trader-wide; Risk and System are selected-account only.")
    else:
        st.info("No approved review trend exists in this analysis period.")
    left, right = st.columns(2)
    with left:
        st.markdown("##### Process quality and outcome")
        points = [item for item in analysis.reviewed_points if item.overall_score is not None and item.result_r is not None]
        if points:
            outcome_labels = {
                "profit": tr("Profit"),
                "loss": tr("Loss"),
                "breakeven": tr("Breakeven"),
            }
            frame = pd.DataFrame([{
                "Process score": float(item.overall_score), "Result R": float(item.result_r),
                "Direction key": item.direction.casefold(), "Outcome key": item.outcome,
                "Direction": tr(direction_tag(item.direction).label), "Outcome": outcome_labels[item.outcome],
                "Review": "Manual" if item.review_kind == "manual_review" else "Auto", "Closed": item.closed,
                "Classification": item.classification or "Unclassified",
            } for item in points])
            chart = alt.Chart(frame).mark_point(filled=True, size=90).encode(
                x=alt.X("Process score:Q", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("Result R:Q"),
                color=alt.Color("Outcome key:N", legend=None, scale=alt.Scale(domain=["profit", "loss", "breakeven"], range=["#0e9163", "#c73545", "#6b7280"])),
                shape=alt.Shape("Direction key:N", legend=None, scale=alt.Scale(domain=["long", "short"], range=["triangle-up", "triangle-down"])),
                tooltip=["Closed", "Direction", "Outcome", "Review", "Process score", "Result R", "Classification"],
            ).properties(height=300)
            st.altair_chart(chart, width="stretch")
            st.caption(
                f"Color: {tr('Profit')} / {tr('Loss')} / {tr('Breakeven')} · "
                f"Shape: {tr('Long')} / {tr('Short')}. Only reviewed trades with standard 1R are plotted. "
                "A positive result does not prove good process, and a loss does not prove poor process."
            )
        else:
            st.caption("Approve reviews and configure standard risk to compare process score with outcome R.")
    with right:
        st.markdown("##### Quality/outcome distribution")
        if analysis.classifications:
            st.bar_chart(pd.DataFrame({"Classification": [item.label for item in analysis.classifications], "Trades": [item.count for item in analysis.classifications]}).set_index("Classification"), width="stretch")
        else:
            st.caption("No reviewed classifications in this period.")
    st.markdown("##### Recurring reviewed issues")
    if analysis.issues:
        st.bar_chart(pd.DataFrame({"Issue": [VIOLATION_LABELS.get(item.label, HARD_RULE_LABELS.get(item.label, item.label)) for item in analysis.issues], "Trades": [item.count for item in analysis.issues]}).set_index("Issue"), width="stretch")
        st.caption(f"Counts are across {len(analysis.reviewed_points)} reviewed trade(s) in this period; one trade can carry more than one issue.")
    else:
        st.caption("No tagged issues in reviewed trades for this period.")


def _render_monitor_risk(analysis: MonitorAnalysisReport, snapshot: RiskSnapshot) -> None:
    with st.container(horizontal=True, gap="small"):
        st.metric("Daily R", "—" if snapshot.daily_r is None else format_r(snapshot.daily_r), border=True)
        st.metric("Weekly R", "—" if snapshot.weekly_r is None else format_r(snapshot.weekly_r), border=True)
        st.metric("Drawdown", "—" if snapshot.current_drawdown_percent is None else format_percent(snapshot.current_drawdown_percent), border=True)
        st.metric("Loss streak", "—" if snapshot.consecutive_losses is None else str(snapshot.consecutive_losses), border=True)
    left, right = st.columns(2)
    with left:
        st.markdown("##### Review evidence lifecycle")
        if analysis.lifecycle:
            labels = {"manual_review": "Manual", "approved_auto_review": "Approved Auto", "auto_review": "Awaiting approval", "needs_approval": "Requires review"}
            st.bar_chart(pd.DataFrame({"State": [labels.get(item.label, item.label) for item in analysis.lifecycle], "Trades": [item.count for item in analysis.lifecycle]}).set_index("State"), width="stretch")
    with right:
        st.markdown("##### Policy evidence")
        if analysis.policy_states:
            labels = {"within_policy": "Within policy", "over_policy": "Over policy", "unavailable": "Unavailable"}
            st.bar_chart(pd.DataFrame({"State": [labels.get(item.label, item.label) for item in analysis.policy_states], "Trades": [item.count for item in analysis.policy_states]}).set_index("State"), width="stretch")
    st.caption("These are post-close monitoring signals only; they never place, block, or change MT5 orders.")


def _render_monitor_system(analysis: MonitorAnalysisReport) -> None:
    st.markdown("##### Strategy evidence")
    if analysis.strategies:
        frame = pd.DataFrame([{"Strategy": item.label, "Reviewed": item.count, "Process score": None if item.average_process_score is None else float(item.average_process_score), "Win rate": None if item.win_rate is None else float(item.win_rate), "Average R": None if item.average_r is None else float(item.average_r)} for item in analysis.strategies])
        st.bar_chart(frame.set_index("Strategy")[["Process score"]], width="stretch")
        st.dataframe(frame, hide_index=True, width="stretch", column_config={"Process score": st.column_config.NumberColumn(format="%.0f"), "Win rate": st.column_config.NumberColumn(format="%.1f%%"), "Average R": st.column_config.NumberColumn(format="%+.2fR")})
    else:
        st.caption("No reviewed strategy evidence in this period.")
    st.markdown("##### Manual-review context")
    st.caption("Setup, session, and regime are Manual Review fields. Samples below five reviews are directional, not causal evidence.")
    tabs = st.tabs(["Setup", "Session", "Market regime"])
    for tab, dimension in zip(tabs, ("setup", "session", "regime"), strict=True):
        with tab:
            rows = analysis.contexts[dimension]
            if not rows:
                st.caption("Complete Manual Reviews with optional context to populate this report.")
                continue
            frame = pd.DataFrame([{"Context": item.label, "Reviews": item.count, "Process score": item.average_process_score, "Win rate": item.win_rate, "Average R": item.average_r} for item in rows])
            st.bar_chart(frame.set_index("Context")[["Process score"]].astype(float), width="stretch")
            st.dataframe(frame, hide_index=True, width="stretch", column_config={"Process score": st.column_config.NumberColumn(format="%.0f"), "Win rate": st.column_config.NumberColumn(format="%.1f%%"), "Average R": st.column_config.NumberColumn(format="%+.2fR")})


def _render_framework_focus(repo: SQLiteJournalRepository, account: AccountListItem, service: FrameworkService, scores: tuple[PillarScore, ...], *, compact: bool = False) -> None:
    service.ensure_coaching_focus(account.id)
    focus, progress = service.focus_progress(account.id)
    focus_heading = "Today's coaching action" if compact else "Coaching focus"
    st.markdown(f"##### {tr(focus_heading)}")
    if focus is not None and focus.pillar in {"risk", "system"} and focus.account_id != account.id:
        st.info(tr("An active {pillar} coaching focus applies to another account. Select that account in Settings to review it.", pillar=tr(PILLAR_NAMES[focus.pillar])))
        return
    if focus is not None and progress is not None:
        with st.container(border=True):
            kind = {"manual_evidence": "Reviewed evidence", "component": "Pillar component", "criterion": "Criterion", "violation": "Issue"}[focus.metric_kind]
            st.markdown(f"**{tr(PILLAR_NAMES[focus.pillar])} · {tr(kind)}**")
            st.write(tr(focus.action_text))
            st.caption(f"{tr('Why now:')} {tr(focus.coach_reason or focus.hypothesis)}")
            current = "—" if progress.current_value is None else progress.current_value
            st.metric(tr("Reviewed trades collected"), f"{progress.reviews_completed}/{progress.target_reviews}", f"{tr('Current metric:')} {current}", border=True)
            st.caption(f"{tr('Baseline:')} {focus.baseline_value or '—'} · {tr('Target:')} {focus.target_value}")
            with st.form(f"edit-framework-focus-{focus.id}", border=False):
                action = st.text_area(tr("Tailor the next-trade action"), value=focus.action_text)
                if st.form_submit_button(tr("Save action")):
                    repo.update_framework_focus_action(focus_id=focus.id, action_text=action)
                    st.rerun()
            if progress.ready_to_evaluate:
                with st.form(f"resolve-framework-focus-{focus.id}"):
                    outcome = st.segmented_control("Focus outcome", ["completed", "abandoned"], default="completed", required=True)
                    note = st.text_area("Focus reflection", placeholder="What changed, and what will you carry forward?")
                    if st.form_submit_button("Resolve focus", type="primary"):
                        try:
                            repo.resolve_framework_focus(focus_id=focus.id, outcome=outcome, resolution_note=note)
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.toast("Framework focus resolved.")
                            st.rerun()
            history = [item for item in repo.list_framework_focuses() if item.status != "active"]
            if history and not compact:
                with st.expander(tr("Coaching history")):
                    for item in history[:5]:
                        st.markdown(f"**{item.status.capitalize()} · {PILLAR_NAMES[item.pillar]}** — {item.action_text}")
                        if item.resolution_note:
                            st.caption(item.resolution_note)
        return
    st.success(tr("On track: no coaching intervention is required from the current reviewed evidence."))


def _render_period_reviews(repo: SQLiteJournalRepository, account: AccountListItem, service: FrameworkService) -> None:
    st.markdown("##### Weekly and monthly review")
    statuses = [service.period_review_status(account.id, cadence) for cadence in ("weekly", "monthly")]
    with st.container(horizontal=True, gap="small"):
        for status in statuses:
            st.metric(tr(f"{status.cadence.capitalize()} review"), tr("Due" if status.due else "Up to date"), f"{status.period_start} to {status.period_end}", border=True)
    due = next((status for status in statuses if status.due), None)
    if due is not None:
        with st.form(f"period-review-{account.id}-{due.cadence}"):
            st.caption(tr("Save the {cadence} reflection for {start} to {end}.", cadence=tr(due.cadence), start=due.period_start, end=due.period_end))
            note = st.text_area("Review note", placeholder="What pattern did the data reveal?")
            action = st.text_area("One priority corrective action", placeholder="Choose one focused action for the next period.")
            submitted = st.form_submit_button("Save period review", type="primary")
        if submitted:
            try:
                with st.spinner(tr("Saving…")):
                    service.save_period_review(account_id=account.id, cadence=due.cadence, review_note=note, priority_action=action)
            except ValueError as error:
                st.error(str(error))
            else:
                st.toast(tr("Period review saved."))
                st.success("Period review saved.")
                st.rerun()
    reviews = repo.list_framework_period_reviews(account.id)
    if reviews:
        latest = reviews[0]
        with st.expander("Latest saved period review"):
            st.caption(f"{tr(latest.cadence.capitalize())} · {latest.period_start} to {latest.period_end} · {tr('Readiness').casefold()} {_score_text(latest.readiness_score)}")
            st.write(latest.review_note)
            st.markdown(f"**{tr('Priority action:')}** {latest.priority_action}")


def _render_roadmap(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    statuses = {item.pillar: item for item in service.roadmap_status(account.id)}
    st.markdown("#### Readiness roadmap")
    st.caption("Define, Test, Execute, Measure, then Optimize. Most steps are detected automatically as you use the journal — only a few still need your own note.")

    for pillar, name in PILLAR_NAMES.items():
        status = statuses[pillar]
        with st.container(border=True):
            st.markdown(f"##### {tr(name)}")
            for level, column in zip(range(1, 6), st.columns(5), strict=True):
                with column:
                    level_name = tr(ROADMAP_LEVEL_NAMES[level])
                    if level < status.current_level:
                        st.markdown(f":green[✓ {level_name}]")
                    elif level == status.current_level:
                        st.markdown(f"**▶ {level_name}**")
                    else:
                        st.markdown(f":gray[🔒 {level_name}]")
            st.caption(f"{status.completed_items}/{status.total_items} {tr('evidence complete')}")

            if status.completed_items == status.total_items:
                st.success(tr("All roadmap evidence is complete. Continue monitoring the current sample."), icon=":material/check_circle:")
                continue

            for item in (entry for entry in status.items if entry.level == status.current_level):
                if item.is_auto:
                    detail = item.evidence_summary or tr("Not yet detected.")
                    text = f"**{tr(item.label)}**\n\n{detail}"
                    if item.completed:
                        st.success(text, icon=":material/check_circle:")
                    else:
                        st.info(text, icon=":material/hourglass_empty:")
                else:
                    with st.form(f"roadmap-{pillar}-{item.level}-{item.item_key}"):
                        st.markdown(f"**{tr(item.label)}**")
                        complete = st.checkbox(tr("I completed this step"), value=item.completed)
                        note = st.text_area(
                            tr("Evidence note"),
                            value=item.evidence_summary or "",
                            placeholder=tr("Briefly record the evidence for this step."),
                        )
                        submitted = st.form_submit_button(tr("Mark complete"), type="primary")
                    if submitted:
                        if not complete:
                            st.warning(tr("Confirm completion before saving this roadmap item."))
                        else:
                            try:
                                with st.spinner(tr("Saving…")):
                                    service.save_pillar_roadmap_evidence(
                                        account_id=account.id,
                                        pillar=pillar,
                                        level=item.level,
                                        item_key=item.item_key,
                                        completed=True,
                                        evidence_note=note,
                                    )
                            except ValueError as error:
                                st.error(str(error))
                            else:
                                completed_message = tr("{name} roadmap item completed.", name=tr(name))
                                st.toast(completed_message)
                                st.success(completed_message)
                                st.rerun()

            history_items = [item for item in status.items if item.completed]
            if history_items:
                with st.expander(tr("Completed evidence"), icon=":material/history:"):
                    for item in sorted(history_items, key=lambda entry: (entry.level, entry.item_key)):
                        tag = tr("Auto") if item.is_auto else tr("Manual")
                        detail = f" — {item.evidence_summary}" if item.evidence_summary else ""
                        st.markdown(f"- {tr('Level')} {item.level} · {tag}: {tr(item.label)}{detail}")
def _render_risk_policy(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    policy = repo.get_active_risk_policy(account.id)
    funded = repo.get_account_funded_capital(account.id)
    st.markdown("#### Account risk policy")
    st.caption("The policy defines reporting 1R and safety limits. It monitors closed MT5 trades only and never controls MT5.")
    if funded is None:
        st.warning("Set funded capital in Settings → Account & risk before saving a Risk policy.")
    else:
        st.info(f"Funded capital: {funded} {account.account_currency}.")
    with st.form(f"account-risk-policy-{account.id}"):
        first, second, third = st.columns(3)
        standard = first.number_input("Standard risk (1R) (%)", min_value=0.01, value=float(policy.standard_risk_per_trade_percent) if policy else 2.0, step=0.05)
        maximum = second.number_input("Maximum risk per trade (%)", min_value=0.01, value=float(policy.maximum_risk_per_trade_percent) if policy else 3.0, step=0.05)
        minimum_rr = third.number_input("Minimum R:R", min_value=0.01, value=float(policy.minimum_rr) if policy else 1.0, step=0.1)
        st.markdown("**Hard limits**")
        first, second, third, fourth = st.columns(4)
        daily = first.number_input("Daily loss limit (R)", min_value=0.01, value=float(policy.daily_loss_limit_r) if policy else 5.0, step=0.25)
        weekly = second.number_input("Weekly loss limit (R)", min_value=0.01, value=float(policy.weekly_loss_limit_r) if policy else 20.0, step=0.25)
        drawdown = third.number_input("Maximum drawdown (%)", min_value=0.01, value=float(policy.max_drawdown_percent) if policy else 30.0, step=0.5)
        streak = fourth.number_input("Maximum loss streak", min_value=1, value=policy.max_consecutive_losses if policy else 10, step=1)
        with st.expander("Reference-only open-risk controls"):
            open_risk = st.number_input("Maximum open risk (R)", min_value=0.01, value=float(policy.max_open_risk_r) if policy else 1.0, step=0.25)
            correlation = st.text_area("Correlation / exposure policy", value=policy.correlation_policy if policy and policy.correlation_policy else "")
            st.caption("The closed-trade MT5 exporter cannot verify open risk or correlation exposure automatically.")
        pretrade_balance_evidence = st.checkbox(
            "Use MT5 pre-trade balance as advisory no-SL risk evidence",
            value=policy.pretrade_balance_auto_evidence_enabled if policy else False,
            help="Uses only the balance captured by MT5 immediately before entry. It does not create a post-trade review or modify the imported stop loss.",
        )
        submitted = st.form_submit_button("Save risk policy", type="primary")
    if submitted:
        try:
            with st.spinner(tr("Saving…")):
                repo.save_account_risk_policy(
                    account_id=account.id, standard_risk_per_trade_percent=str(standard), maximum_risk_per_trade_percent=str(maximum),
                    daily_loss_limit_r=str(daily), weekly_loss_limit_r=str(weekly), max_drawdown_percent=str(drawdown),
                    max_open_risk_r=str(open_risk), max_consecutive_losses=int(streak), minimum_rr=str(minimum_rr), correlation_policy=correlation,
                    pretrade_balance_auto_evidence_enabled=pretrade_balance_evidence,
                )
        except ValueError as error:
            st.error(str(error))
        else:
            st.toast(tr("Risk policy saved as a new version."))
            st.success("Risk policy saved as a new version.")
            st.rerun()


def _render_framework_rules(repo: SQLiteJournalRepository) -> None:
    settings = repo.get_framework_rule_settings()
    st.markdown("#### Review rules")
    st.caption("The four hard-rule toggles below affect new or corrected assessments only — their effective result is snapshotted when an assessment is saved, so later changes never rewrite an already-saved review. The violation-count threshold is different: it is read live and can change the Caution cap for trades still inside the current rolling Monitor window. Neither ever locks MT5 trading.")
    with st.form("framework-rule-settings"):
        revenge = st.checkbox("Oversized revenge trade is a hard Psychology and Risk failure", value=settings.oversized_revenge_hard)
        setup = st.checkbox("Mandatory setup absent is a hard System failure", value=settings.mandatory_setup_hard)
        stop = st.checkbox("Deliberately widened stop is a hard Risk failure", value=settings.stop_widened_hard)
        shutdown = st.checkbox("Trading after a hard shutdown is a hard Risk failure", value=settings.shutdown_breach_hard)
        threshold = st.number_input("Repeated critical violations before numeric cap", min_value=2, value=settings.repeated_critical_threshold, step=1)
        submitted = st.form_submit_button("Save framework rules", type="primary")
    if submitted:
        try:
            with st.spinner(tr("Saving…")):
                repo.save_framework_rule_settings(
                    oversized_revenge_hard=revenge, mandatory_setup_hard=setup, stop_widened_hard=stop,
                    shutdown_breach_hard=shutdown, repeated_critical_threshold=int(threshold),
                )
        except ValueError as error:
            st.error(str(error))
        else:
            st.toast(tr("Review rules saved."))
            st.success("Review rules saved.")
            st.rerun()
