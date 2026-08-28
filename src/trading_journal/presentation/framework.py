"""Native Streamlit presentation for the greenfield three-pillar framework."""

from __future__ import annotations

from collections import Counter
from collections.abc import MutableMapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import altair as alt

from trading_journal.application.framework import (
    LEGACY_PSYCHOLOGY_ROADMAP_ITEMS,
    PILLAR_NAMES,
    ROADMAP_LEVEL_NAMES,
    FrameworkService,
    MonitorAnalysisReport,
    PillarScore,
    ReadinessAssessment,
    RiskSnapshot,
    TradeProcessScore,
)
from trading_journal.application.display_time import format_relative_time
from trading_journal.application.reporting_time import reporting_datetime
from trading_journal.domain.review_taxonomy import REVIEW_MISTAKE_CODES, REVIEW_MISTAKES_BY_PILLAR
from trading_journal.infrastructure.sqlite_repository import (
    ASSESSMENT_CRITERIA,
    CURRENT_RUBRIC_VERSION,
    LEGACY_RUBRIC_VERSION,
    PSYCHOLOGY_CRITERIA,
    RISK_CRITERIA,
    SYSTEM_CRITERIA,
    AccountListItem,
    ReviewContextSelection,
    SQLiteJournalRepository,
)
from trading_journal.presentation.i18n import format_relative_time_localized, queue_toast, tr
from trading_journal.presentation.formatting import format_count, format_currency, format_exposure_r, format_percent, format_r, format_score
from trading_journal.presentation.browser_timezone import browser_timezone
from trading_journal.presentation.trade_tags import direction_tag, outcome_tag


GRADE_OPTIONS = ("Pass", "Partial", "Fail")
RESET_PERIOD_LABELS = {"Daily": "daily", "Weekly": "weekly", "Monthly": "monthly", "All time": "all_time"}
REVIEW_PAGE_SIZE = 25
CRITERIA_GRID_COLUMNS = 4
PILLAR_ACCENT_COLORS = {"Psychology": "blue", "Risk management": "orange", "Trading system": "violet"}
CRITERION_LABELS = {
    "edge_execution": "Edge execution",
    "risk_acceptance": "Risk acceptance",
    "probability_mindset": "Probability mindset",
    "outcome_independence": "Outcome independence and reset",
    # Legacy labels remain available for rubric-v1 history.
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
CRITERION_HELP = {
    "edge_execution": "When the documented edge appeared, did I execute it without hesitation, chasing, or improvisation?",
    "risk_acceptance": "Before entry, had I genuinely accepted the predefined loss so fear or hope did not alter the trade?",
    "probability_mindset": "Did I treat this trade as one uncertain event in a series rather than needing to predict the outcome?",
    "outcome_independence": "Did I judge the trade by process and reset after its result before making another decision?",
    "policy_adherence": "Was the trade compatible with the account Risk policy?",
    "position_size_accuracy": "Was position size appropriate for the intended risk?",
    "stop_discipline": "Was invalidation defined before entry and was the stop respected rather than widened or ignored?",
    "exposure_limit_compliance": "Were the applicable exposure and loss-limit controls respected?",
    "setup_validity": "Was the documented strategy setup actually present?",
    "context_alignment": "Did market, session, timeframe, and regime meet the strategy rules?",
    "entry_fidelity": "Did entry follow the documented trigger?",
    "management_exit_fidelity": "Were trade management and exit consistent with the strategy?",
}
VIOLATION_LABELS = {
    "fomo_or_chase": "FOMO / chased price",
    "revenge": "Revenge traded",
    "overtrading": "Overtraded",
    "overconfidence_streak": "Became overconfident",
    "fear_hesitation": "Hesitated because of fear",
    "forced_trade": "Forced an impatient trade",
    "post_loss_reset": "Failed to reset after a loss",
    "certainty_seeking": "Needed certainty or tried to predict",
    "risk_not_accepted": "Had not accepted the predefined risk",
    "outcome_attachment": "Let a prior outcome influence the decision",
    "position_size_too_large": "Position size was too large",
    "overtrading_positions": "Opened too many positions",
    "correlation_exposure": "Took too much correlated exposure",
    "no_stop_loss": "Entered without a stop loss",
    "stop_widened": "Moved the stop loss farther away",
    "loss_limit_exceeded": "Exceeded a loss limit",
    "shutdown_breach": "Traded after shutdown",
    "mandatory_setup_absent": "Traded without a valid setup",
    "context_misread": "Misread the trend or market context",
    "entry_timing": "Entered too early or too late",
    "premature_exit": "Took profit too early",
    "held_loser_too_long": "Held a losing trade too long",
    "exit_plan_deviation": "Did not follow the exit plan",
    # Historical labels remain available when an older assessment is edited.
    "emotional_sizing": "Emotional position sizing",
    "ignored_trade_plan": "Deviated from the trade plan",
    "daily_limit": "Daily limit issue",
    "weekly_limit": "Weekly limit issue",
    "drawdown_limit": "Drawdown limit issue",
    "open_exposure": "Open exposure issue",
}
MISTAKE_CATEGORIES = {
    code: {
        "psychology": "Psychology and discipline",
        "risk": "Risk management",
        "system": "Setup and execution",
    }[pillar]
    for pillar, codes in REVIEW_MISTAKES_BY_PILLAR.items()
    for code in codes
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
PERIOD_ALERT_LABELS = {
    "risk_stop": "Risk stop",
    "risk_caution": "Risk caution",
    "risk_unconfigured": "Risk policy not configured",
    "psychology_hard_rule": "Psychology hard-rule failure",
    "risk_hard_rule": "Risk hard-rule failure",
    "system_hard_rule": "Trading system hard-rule failure",
    "psychology_developing": "Psychology developing",
    "risk_developing": "Risk management developing",
    "system_developing": "Trading system developing",
    "weekly_review_due": "Weekly review due",
    "monthly_review_due": "Monthly review due",
}
COMPONENT_DEFINITIONS = {
    "Edge execution": "Average reviewed Edge execution grade.",
    "Risk acceptance": "Average reviewed Risk acceptance grade.",
    "Probability mindset": "Average reviewed Probability mindset grade.",
    "Outcome independence and reset": "Average reviewed Outcome independence and reset grade.",
    "Policy adherence": "Average reviewed Policy adherence grade.",
    "Stop discipline": "Average reviewed Stop discipline grade.",
    "Limit compliance": "100 for a reviewed trade with no historical daily/weekly/drawdown/streak event; 0 when an event occurred.",
    "Exposure control": "Average reviewed Exposure-limit compliance grade.",
    "Setup validity": "Average reviewed Setup validity grade.",
    "Execution fidelity": "Average of Entry and Management/exit grades.",
    "Context alignment": "Average reviewed Context alignment grade.",
    "Edge evidence": "100 when the attached strategy's backtest is marked verified; otherwise 0.",
}


def _account_label(account: AccountListItem) -> str:
    return f"{account.display_name} · {account.login} · {account.broker_server}"


def _reset_period_label(value: str) -> str:
    label = next(label for label, stored in RESET_PERIOD_LABELS.items() if stored == value)
    return tr(label)


def _render_help_popover(*captions: str, icon: str = ":material/help:") -> None:
    """A compact on-demand help trigger, keeping page content focused."""
    with st.popover("?", icon=icon, width="content"):
        for text in captions:
            st.caption(text)


def _score_text(value: str | None) -> str:
    return "—" if value is None else format_score(value)


def _rubric_label(rubric_version: str | None) -> str:
    return tr("Legacy 13-criterion") if rubric_version == LEGACY_RUBRIC_VERSION else tr("Zone-aligned 12-criterion")


def _render_rubric_sample_caption(scores: tuple[PillarScore, ...], window: int) -> None:
    reviewed = min((score.reviewed_total for score in scores), default=0)
    legacy = max((score.legacy_reviewed_total for score in scores), default=0)
    st.caption(
        tr(
            "Zone-aligned sample: {reviewed}/{window} reviewed trades · {legacy} legacy review(s) retained in history and excluded",
            reviewed=reviewed,
            window=window,
            legacy=legacy,
        )
    )


def _review_history_code_label(code: str, *, alert: bool = False) -> str:
    if alert:
        return tr(PERIOD_ALERT_LABELS.get(code, code.replace("_", " ").capitalize()))
    return tr(VIOLATION_LABELS.get(code, HARD_RULE_LABELS.get(code, code.replace("_", " ").capitalize())))


def _focus_metric_text(value: str | None, metric_kind: str) -> str:
    if value is None:
        return "—"
    if metric_kind in {"criterion", "component"}:
        return format_score(value)
    return format_count(int(Decimal(value)))


def _state_label(snapshot: RiskSnapshot) -> str:
    # "Elevated," not "Caution" — a pillar's rolling score can independently show
    # "Caution" (capped by repeated critical violations) at the same time this
    # metric is visible; reusing the same word for two unrelated states would
    # collide on screen.
    return tr({"clear": "Clear", "caution": "Elevated", "stop": "Stop", "unconfigured": "Set up"}[snapshot.state])


def _readiness_metric(readiness: ReadinessAssessment) -> tuple[str | None, str, str]:
    colors = {"ready": "green", "incomplete": "orange", "fail": "red"}
    value = None if readiness.score is None else format_score(readiness.score)
    return value, readiness.status.capitalize(), colors.get(readiness.status, "gray")


def _risk_state_metric(snapshot: RiskSnapshot) -> tuple[str, str, str]:
    details = {
        "clear": ("Within limits", "green"),
        "caution": ("Needs attention", "orange"),
        "stop": ("Limit reached", "red"),
        "unconfigured": ("Required", "orange"),
    }
    detail, color = details[snapshot.state]
    return _state_label(snapshot), detail, color


def _daily_r_metric(value: str | None) -> tuple[str | None, str, str]:
    if value is None:
        return None, "Unavailable", "gray"
    result = Decimal(value)
    if result > 0:
        return format_r(result), "Gain", "green"
    if result < 0:
        return format_r(result), "Loss", "red"
    return format_r(result), "Flat", "gray"


def _drawdown_metric(value: str | None) -> tuple[str | None, str, str]:
    if value is None:
        return None, "Unavailable", "gray"
    drawdown = Decimal(value)
    if drawdown == 0:
        return format_percent(drawdown), "No drawdown", "gray"
    return format_percent(drawdown), "Current monitoring-period maximum", "gray"


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
        "Reviewed Actual risk {amount} is {state}. {policy} It replaces automatic evidence for this logical-trade policy comparison only; account-limit monitoring remains based on aggregate logical-trade outcomes.",
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


def _format_trade_duration(entry_time: str, exit_time: str) -> str:
    """Format an imported trade's elapsed time without changing its stored timestamps."""
    entry = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
    exit_ = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
    total_minutes = max(0, int((exit_ - entry).total_seconds() // 60))
    if total_minutes == 0:
        return "<1m"
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_execution_number(value: str) -> str:
    """Preserve imported precision while adding separators for execution facts."""
    return f"{Decimal(value):,f}"


def _score_scope_label(score: PillarScore, account: AccountListItem) -> str:
    """Describe the evidence scope without implying that a pillar is an aggregate."""
    label = _account_label(account)
    if score.pillar == "risk":
        return tr("Account: {account}", account=label)
    if score.pillar == "system":
        return tr("System: {account}", account=label)
    return tr("Psychology: {account}", account=label)


def _render_score_cards(scores: tuple[PillarScore, ...], account: AccountListItem) -> None:
    for column, score in zip(st.columns(len(scores), gap="small"), scores, strict=True):
        if score.hard_block:
            label = "FAIL"
            delta_color = "red"
        elif score.score is None:
            label = "Incomplete"
            delta_color = "gray"
        elif score.status == "incomplete":
            # A live percentage next to the literal word "Incomplete" reads as
            # self-contradictory — this is a partial-sample early read, not the
            # same "no evidence yet" state as score.score is None.
            label = "Early estimate"
            delta_color = "gray"
        elif score.status == "caution":
            label = "Caution"
            delta_color = "orange"
        else:
            label = score.status.capitalize()
            delta_color = "green"
        delta = (
            tr(
                "{label} · Raw {raw} · {count} in sample",
                label=tr(label),
                raw=_score_text(score.raw_score),
                count=score.sample_size,
            )
            if score.raw_score is not None
            and (score.status == "caution" or score.raw_score != score.score)
            else tr("{label} · {count} in sample", label=tr(label), count=score.sample_size)
        )
        column.metric(
            tr(PILLAR_NAMES[score.pillar]),
            _score_text(score.score),
            delta,
            delta_color=delta_color,
            delta_arrow="off",
            border=True,
        )
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
    with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
        st.caption(tr("Sample size: {sample_line}", sample_line=sample_line))
        if any(blocked) or any(capped) or any(score.score is None for score in scores):
            with st.popover("?", icon=":material/help:", width="content"):
                if any(blocked) and any(capped):
                    st.caption(tr("Pillars marked ✕ in red have an active hard-rule failure; pillars marked ◆ in amber are capped by repeated critical violations — neither score reflects readiness."))
                elif any(blocked):
                    st.caption(tr("Pillars marked ✕ in red have an active hard-rule failure — their score does not reflect readiness."))
                elif any(capped):
                    st.caption(tr("Pillars marked ◆ in amber are capped at 59 by repeated critical violations — see the detail below the score card."))
                elif any(score.score is None for score in scores):
                    st.caption(tr("Pillars without a scored sample yet show as 0 on this chart."))


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
    policy = repo.get_active_risk_policy(account.id)
    st.markdown(tr("#### Three-pillar monitor"))
    st.caption(tr("This compact view always uses a fixed 20-trade window. Open Bearings → Monitor to adjust the rolling sample."))
    _render_rubric_sample_caption(scores, 20)
    _render_risk_configuration_notice(service, account.id)
    readiness_value, readiness_delta, readiness_color = _readiness_metric(readiness)
    state_value, state_delta, state_color = _risk_state_metric(snapshot)
    daily_value, daily_delta, daily_color = _daily_r_metric(snapshot.daily_r)
    drawdown_value, drawdown_delta, drawdown_color = _drawdown_metric(snapshot.max_drawdown_percent)
    with st.container(horizontal=True, gap="small"):
        st.metric(
            tr("Overall readiness"), readiness_value, tr(readiness_delta),
            delta_color=readiness_color, delta_arrow="off", border=True,
        )
        st.metric(
            tr("Risk state"), state_value, tr(state_delta),
            delta_color=state_color, delta_arrow="off", border=True,
        )
        st.metric(
            tr("Today"), daily_value, tr(daily_delta),
            delta_color=daily_color,
            delta_arrow="off",
            delta_description=None if policy is None else f"{format_exposure_r(policy.daily_loss_limit_r)} daily loss limit",
            border=True,
        )
        st.metric(
            tr("Max drawdown"), drawdown_value, tr(drawdown_delta),
            delta_color=drawdown_color,
            delta_arrow="off",
            delta_description=None if not snapshot.configured else tr("Resets {period}", period=_reset_period_label(snapshot.drawdown_reset_period).lower()),
            border=True,
        )
    _render_score_cards(scores, account)
    _render_pillar_radar(scores)
    st.caption(tr(readiness.detail))


def _render_framework_page_header(repo: SQLiteJournalRepository) -> AccountListItem | None:
    st.markdown('<div class="dashboard-kicker">POST-TRADE JOURNAL</div>', unsafe_allow_html=True)
    st.subheader(tr("Three-pillar framework"))
    st.caption(tr("Use completed MT5 trades to assess execution. Alerts are advisory; this journal never sends, blocks, or changes MT5 orders."))
    account = repo.get_active_mt5_account()
    if account is None:
        st.info(tr("Add an approved MT5 account in Settings before using the framework."))
        st.page_link("app_pages/settings.py", label=tr("Go to Settings"), icon=":material/settings:")
        return None
    st.caption(tr("Reviewing {account}. Change the active account in Settings → Approved MT5 accounts.", account=_account_label(account)))
    return account


def render_framework_review_page(repo: SQLiteJournalRepository) -> None:
    account = _render_framework_page_header(repo)
    if account is None:
        return
    _render_post_trade_review(repo, account)


def render_framework_monitor_page(repo: SQLiteJournalRepository) -> None:
    account = _render_framework_page_header(repo)
    if account is None:
        return
    _render_monitor(repo, account)


def render_framework_improve_page(repo: SQLiteJournalRepository) -> None:
    account = _render_framework_page_header(repo)
    if account is None:
        return
    _render_roadmap(repo, account)


def _render_post_trade_review(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    st.markdown("#### Closed-trade reviews")
    profiles = [repo.get_account_strategy(account.id)]
    trades = repo.list_closed_trades_for_review(account.id)
    if not trades:
        st.info(tr("No completed MT5 positions have been imported for this account yet."))
        return
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


def _logical_trade_select_all_key(account_id: int) -> str:
    return f"logical-trade-select-all-{account_id}"


def _logical_trade_page_key(account_id: int) -> str:
    return f"logical-trade-page-{account_id}"


def _clear_logical_trade_selection(account_id: int) -> None:
    """Clear singleton-selection widgets before their next render."""
    prefix = _logical_trade_selection_prefix(account_id)
    st.session_state.pop(_logical_trade_selection_store_key(account_id), None)
    st.session_state.pop(_logical_trade_select_all_key(account_id), None)
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


def _toggle_logical_trade_selection(
    account_id: int,
    logical_trade_id: int,
    visible_trade_ids: Sequence[int] = (),
) -> None:
    selected = set(st.session_state.get(_logical_trade_selection_store_key(account_id), ()))
    checkbox_key = f"{_logical_trade_selection_prefix(account_id)}{logical_trade_id}"
    if st.session_state.get(checkbox_key, False):
        selected.add(logical_trade_id)
    else:
        selected.discard(logical_trade_id)
    st.session_state[_logical_trade_selection_store_key(account_id)] = tuple(sorted(selected))
    if visible_trade_ids:
        st.session_state[_logical_trade_select_all_key(account_id)] = set(visible_trade_ids).issubset(selected)


def _toggle_all_logical_trade_selection(account_id: int, visible_trade_ids: Sequence[int]) -> None:
    """Select or clear every trade in the current filtered register."""

    select_all_key = _logical_trade_select_all_key(account_id)
    selected = tuple(visible_trade_ids) if st.session_state.get(select_all_key, False) else ()
    st.session_state[_logical_trade_selection_store_key(account_id)] = selected
    prefix = _logical_trade_selection_prefix(account_id)
    selected_set = set(selected)
    for trade_id in visible_trade_ids:
        st.session_state[f"{prefix}{trade_id}"] = trade_id in selected_set


def _change_logical_trade_page(account_id: int, change: int, page_count: int) -> None:
    current = int(st.session_state.get(_logical_trade_page_key(account_id), 1))
    st.session_state[_logical_trade_page_key(account_id)] = max(1, min(page_count, current + change))


def _render_bulk_quick_review_dialog(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore]) -> None:  # type: ignore[no-untyped-def]
    confirmation = st.session_state.get(_bulk_quick_review_key(account.id))
    if confirmation is None or confirmation.get("account_id") != account.id:
        return
    selected_ids = set(confirmation.get("trade_ids", ()))
    selected = [(trade, scores[trade.id]) for trade in trades if trade.id in selected_ids]
    eligible = [(trade, score) for trade, score in selected if score.review_kind in {"auto_review", "needs_approval"}]
    if not eligible:
        st.info(tr("None of the selected trades still has automatic evidence available for Quick Review."))
        if st.button(tr("Close"), key=f"close-bulk-quick-review-{account.id}"):
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
    st.caption(tr("Quick Review saves the displayed automatic evidence as approved review evidence. A Manual Review can still replace it later."))
    with st.container(horizontal=True, horizontal_alignment="right"):
        cancel = st.button(tr("Cancel"), key=f"cancel-bulk-quick-review-{account.id}")
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
                    account_id=account.id, trade_id=trade.id, risk_policy_id=score.auto_risk.policy_id,
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
    queue_toast(message)
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


def _grade_control(label: str, *, existing: str | None, key: str, help_text: str | None = None) -> str | None:
    # Only pass a default on this widget's first render. Once a value is stored under `key`
    # (e.g. by a "Mark as Pass" button's on_click), passing a non-None default alongside it is
    # ambiguous to Streamlit and logs a "default value but also set via Session State" warning.
    default = existing.capitalize() if existing and key not in st.session_state else None
    choice = st.segmented_control(
        label,
        GRADE_OPTIONS,
        format_func=tr,
        default=default,
        key=key,
        help=None if help_text is None else tr(help_text),
        width="content",
    )
    return None if choice is None else choice.casefold()


def _review_context_option(options: Sequence[object], saved_name: str | None) -> object | None:
    """Restore a saved context snapshot when its active option still exists."""

    if saved_name is None:
        return None
    return next((option for option in options if getattr(option, "name", None) == saved_name), None)


def _review_context_option_label(option: object | None) -> str:
    return "" if option is None else str(getattr(option, "name"))


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
    if existing_manual is not None and existing_manual.rubric_version == LEGACY_RUBRIC_VERSION:
        st.info(
            tr(
                "This review uses the legacy 13-criterion rubric. Saving a correction preserves it in history and creates a Zone-aligned 12-criterion review; rate the four new Psychology criteria explicitly."
            ),
            icon=":material/history:",
        )
    strategy = repo.get_account_strategy(account.id)
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
    st.markdown(tr("##### Assessment"))
    st.caption(f"Trading system: **{strategy.name}** (bound to this account)")
    st.caption("\\* Required")
    with st.form(f"post-trade-assessment-{trade.id}"):
        active_setups = repo.list_strategy_setups(strategy.id)
        active_sessions = repo.list_review_context_tags("session")
        active_regimes = repo.list_review_context_tags("regime")
        setup_options = [None, *active_setups]
        session_options = [None, *active_sessions]
        regime_options = [None, *active_regimes]
        context_defaults = {
            f"assessment-{trade.id}-setup": _review_context_option(
                active_setups, existing_manual.setup_snapshot if existing_manual else None
            ),
            f"assessment-{trade.id}-session": _review_context_option(
                active_sessions, existing_manual.session_snapshot if existing_manual else None
            ),
            f"assessment-{trade.id}-regime": _review_context_option(
                active_regimes, existing_manual.regime_snapshot if existing_manual else None
            ),
        }
        for context_key, context_default in context_defaults.items():
            if context_key not in st.session_state:
                st.session_state[context_key] = context_default
        context_left, context_middle, context_right = st.columns(3)
        selected_setup = context_left.selectbox(
            "Setup (optional)", setup_options, format_func=_review_context_option_label,
            key=f"assessment-{trade.id}-setup",
        )
        selected_session = context_middle.selectbox(
            "Session (optional)", session_options, format_func=_review_context_option_label,
            key=f"assessment-{trade.id}-session",
        )
        selected_regime = context_right.selectbox(
            "Market regime (optional)", regime_options, format_func=_review_context_option_label,
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
        st.caption(tr("Mark a pillar as Pass, then change only the Partial or Fail exceptions."))
        st.form_submit_button(
            "Mark all criteria as Pass",
            key=f"assessment-{trade.id}-pass-all",
            icon=":material/done_all:",
            type="primary",
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
                type="primary",
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
                            help_text=CRITERION_HELP.get(criterion),
                        )
        legacy_mistakes = tuple(
            code for code in (existing_manual.violation_codes if existing_manual else ()) if code not in REVIEW_MISTAKE_CODES
        )
        mistake_options = REVIEW_MISTAKE_CODES + legacy_mistakes
        with st.container(border=True):
            st.markdown(f"##### {tr('Mistakes and rule breaches')}")
            violation_codes = st.multiselect(
                tr("Trading mistakes"),
                options=mistake_options,
                default=list(existing_manual.violation_codes) if existing_manual else [],
                format_func=lambda code: f"{tr(MISTAKE_CATEGORIES.get(code, 'Earlier review'))} · {tr(VIOLATION_LABELS[code])}",
                placeholder=tr("Select any mistakes made"),
                help=tr("Choose all mistakes that affected this trade. Leave empty if the trade followed your plan."),
            )
            hard_rules = st.multiselect(
                tr("Hard-rule events"),
                options=available_hard_rules,
                default=list(existing_manual.hard_rule_codes) if existing_manual else [],
                format_func=lambda code: tr(HARD_RULE_LABELS[code]),
                help=tr("Enabled events selected on save set Hard-rule status to Fail. That result is snapshotted for this assessment, so later Review rules changes do not rewrite it. Automatic Risk limits are monitoring evidence, not hard failures by themselves."),
            )
            if not available_hard_rules:
                st.caption(tr("No hard-rule events are enabled. Enable one in Settings → Review rules to record it on a new assessment."))
        with st.container(border=True):
            st.markdown(f"##### {tr('Reflection and action')}")
            note = st.text_area(
                f"{tr('What happened and what did you learn?')} *",
                value=existing_manual.post_review_note if existing_manual else "",
                placeholder=tr("Describe execution independently of P&L."),
            )
            action = st.text_area(
                tr("Corrective action"),
                value=existing_manual.corrective_action if existing_manual and existing_manual.corrective_action else "",
                placeholder=tr("Required when any criterion is Partial or Fail, or a hard rule is selected."),
            )
        with st.container(border=True):
            st.markdown(f"##### {tr('Risk evidence')}")
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
                st.caption(tr("No active Risk policy is attached; the assessment still records your judgement, while automatic limit checks remain unavailable."))
            actual_risk = st.text_input(
                tr("Actual risk amount (optional)"),
                value=existing_manual.declared_actual_risk_amount if existing_manual and existing_manual.declared_actual_risk_amount else "",
                placeholder=tr("Enter a verified amount when automatic evidence is not sufficient"),
                help=tr("Overrides automatic evidence for this logical trade's policy comparison. It does not rewrite imported MT5 member positions or logical-trade account-limit monitoring."),
            )
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
        st.error(tr("Rate every criterion as Pass, Partial, or Fail before saving."))
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
        st.error(tr(str(error)))
    else:
        st.session_state["post-trade-review-notice"] = "Post-trade assessment saved."
        queue_toast(tr("Post-trade assessment saved."))
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
            st.markdown(
                f"**{tr('Version {version}', version=revision.version)}** · "
                f"{_rubric_label(revision.rubric_version)} · {revision.archived_at[:19]} · {strategy_label}"
            )
            hard_rule_text = ", ".join(tr(HARD_RULE_LABELS.get(code, code)) for code in revision.hard_rule_codes) or tr("none")
            st.caption(tr("{failed} failed criterion/criteria · Hard rules: {hard_rules}", failed=failed, hard_rules=hard_rule_text))
            if revision.post_review_note:
                st.write(revision.post_review_note)
        for assessment in superseded:
            positions = ", ".join(f"#{position_id}" for position_id in assessment.assessed_position_ids)
            st.markdown(
                f"**{tr('Superseded assessment')}** · {_rubric_label(assessment.rubric_version)} · "
                f"{assessment.superseded_at[:19] if assessment.superseded_at else '—'} · {assessment.assessed_trade_label}"
            )
            reason = tr(assessment.superseded_reason) if assessment.superseded_reason else tr("Logical-trade membership changed")
            st.caption(tr("Assessed {positions} · {reason}", positions=positions, reason=reason))
            if assessment.post_review_note:
                st.write(assessment.post_review_note)


def _render_logical_trade_group_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    existing_group=None,
    selected_position_trade_ids: tuple[int, ...] = (),
    selected_logical_trade_ids: tuple[int, ...] = (),
) -> None:  # type: ignore[no-untyped-def]
    """Create or regroup a logical trade; imported MT5 positions stay immutable."""
    existing_members = () if existing_group is None else existing_group.members
    positions = repo.list_imported_positions_for_grouping(account.id)
    units = repo.list_closed_trades_for_review(account.id)
    unit_by_id = {unit.id: unit for unit in units}
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
    selected_logical_trade_ids = tuple(
        logical_trade_id for logical_trade_id in selected_logical_trade_ids if logical_trade_id in unit_by_id
    )
    if existing_group is None and selected_logical_trade_ids:
        selected_position_trade_ids = tuple(
            member.id
            for logical_trade_id in selected_logical_trade_ids
            for member in unit_by_id[logical_trade_id].members
        )
    else:
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
    is_selection_create = existing_group is None and len(selected_logical_trade_ids) >= 2
    if existing_group is None and selected_position_trade_ids and not is_selection_create:
        st.warning(tr("At least two selected logical trades are required. Return to the register and select the trades again."))
        return
    st.caption(
        "The selected logical trades will be combined into a new logical trade. Each selected trade moves with all of its positions."
        if is_selection_create
        else "Every imported position starts as one logical trade. Select the current members for this logical trade; "
        "selected positions may be moved from another group. Members must share account, symbol, direction, and imported Risk-policy version."
    )
    if is_selection_create:
        selected_units = [unit_by_id[logical_trade_id] for logical_trade_id in selected_logical_trade_ids]
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        tr("Logical trade"): f"LT-{unit.id}",
                        tr("Trade"): unit.display_label,
                        tr("Positions"): unit.position_count,
                        tr("Position IDs"): ", ".join(f"#{position_id}" for position_id in unit.position_ids),
                    }
                    for unit in selected_units
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption("A new logical-trade ID will be created. Source labels are not carried forward automatically.")
    with st.form(f"logical-trade-group-{account.id}-{editor_id}"):
        label = st.text_input(
            "Trade label (optional)",
            value="" if existing_group is None else existing_group.custom_label or "",
            placeholder="e.g. London breakout scale-in",
        )
        if is_selection_create:
            selected = list(selected_position_trade_ids)
        else:
            selected = st.multiselect(
                "Positions",
                options=list(labels),
                default=[member.id for member in existing_members] if existing_group is not None else [],
                format_func=labels.get,
                placeholder="Choose two or more positions",
            )
        save = st.form_submit_button(
            "Create new logical trade" if is_selection_create else "Create logical trade" if existing_group is None else "Continue",
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
                source_logical_trade_ids=selected_logical_trade_ids if is_selection_create else (),
            )
            mode = "merge" if is_selection_create else "regroup"
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["logical-trade-regroup-confirmation"] = {
        "account_id": account.id,
        "logical_trade_id": editor_id,
        "position_trade_ids": tuple(selected),
        "source_logical_trade_ids": selected_logical_trade_ids if is_selection_create else (),
        "display_label": label,
        "mode": mode,
        "affected_assessment_count": preview.affected_assessment_count,
        "affected_assessment_labels": preview.affected_assessment_labels,
    }
    st.rerun()


def _render_logical_trade_regroup_confirmation(repo: SQLiteJournalRepository, account: AccountListItem, confirmation: dict) -> None:  # type: ignore[type-arg]
    count = confirmation["affected_assessment_count"]
    if confirmation["mode"] == "disband":
        st.warning(tr("This will split the current logical trade into individual position trades."))
    elif confirmation["mode"] == "merge":
        st.warning(tr("This will merge the selected logical trades into a new logical trade."))
    else:
        st.warning(tr("This will apply the selected current membership to the logical trade."))
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
            {
                "disband": "Confirm disband",
                "merge": "Confirm & review",
            }.get(confirmation["mode"], "Confirm & review"),
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
                    source_logical_trade_ids=tuple(confirmation.get("source_logical_trade_ids", ())),
                    expected_assessment_count=confirmation["affected_assessment_count"],
                )
                notice = (
                    "Logical trades merged into a new logical trade."
                    if confirmation["mode"] == "merge"
                    else "Logical trade saved."
                )
        if result.superseded_assessment_count:
            notice += f" {result.superseded_assessment_count} assessment(s) now need re-review."
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["post-trade-review-notice"] = notice
    if confirmation["mode"] != "disband" and result.logical_trade_id is not None:
        st.session_state["post-trade-review-trade-id"] = result.logical_trade_id
        st.session_state["post-trade-review-queue"] = ()
    queue_toast(tr(notice))
    _defer_logical_trade_selection_reset(account.id)
    _clear_group_dialog()
    st.rerun()


def _mobile_field_label(container, label: str) -> None:  # type: ignore[no-untyped-def]
    """Print a field label that only shows once the 9-column trade row has stacked.

    On desktop the header row above the table already labels every column, so this
    stays hidden there (see the CSS in _render_review_register) and only appears
    once Streamlit's column-stacking collapses each field onto its own full-width
    line, where the header row's labels are no longer adjacent to their values.
    """
    container.markdown(f'<span class="trade-review-field-label">{tr(label)}</span>', unsafe_allow_html=True)


def _render_review_register(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore], profiles) -> None:  # type: ignore[no-untyped-def]
    st.html(
        """
        <style>
        /* Below Streamlit's column-stacking breakpoint, the 9-column trade row
           collapses into 9 full-width blocks in DOM order. The header row (which
           only prints its labels once, above all rows) then reads as a detached
           list, and every trade's values lose their labels entirely. Print a small
           inline label ahead of each field's value - hidden on desktop, where the
           header row already does that job - and hide the now-redundant header row
           at that width instead. */
        .trade-review-field-label { display: none; }
        @media (max-width: 640px) {
            .trade-review-field-label {
                display: block;
                font-size: 0.72rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.03em;
                opacity: 0.65;
                margin-bottom: 0.1rem;
            }
            div.st-key-trade-review-table-header { display: none; }
        }
        </style>
        """
    )
    active_policy = repo.get_active_risk_policy(account.id)
    ordered = sorted(trades, key=lambda item: (item.exit_time, item.id), reverse=True)
    review_kind_to_filter_key = {
        "needs_approval": "needs_approval",
        "auto_review": "auto_reviewed",
        "approved_auto_review": "manual_reviewed",
        "manual_review": "manual_reviewed",
    }
    groups = {
        "needs_approval": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].review_kind == "needs_approval"],
        "auto_reviewed": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].review_kind == "auto_review"],
        "manual_reviewed": [
            (trade, scores[trade.id])
            for trade in ordered
            if scores[trade.id].review_kind in {"approved_auto_review", "manual_review"}
        ],
    }
    _prepare_logical_trade_register_state(account.id)
    filter_order = ("needs_approval", "auto_reviewed", "manual_reviewed")
    filter_names = {
        "needs_approval": "Requires review",
        "auto_reviewed": "Auto-reviewed",
        "manual_reviewed": "Reviewed",
    }
    filter_labels = {key: f"{tr(filter_names[key])} ({len(items)})" for key, items in groups.items()}
    with st.container(horizontal=True, gap="small"):
        checked_by_key = {
            key: st.checkbox(
                filter_labels[key],
                value=(key == "needs_approval"),
                key=f"review-filter-{key}-{account.id}",
            )
            for key in filter_order
        }
    selected_keys = tuple(key for key in filter_order if checked_by_key[key])
    filter_key = f"logical-trade-selection-filter-{account.id}"
    current_filter = selected_keys
    previous_filter = st.session_state.get(filter_key)
    if previous_filter is not None and previous_filter != current_filter:
        _clear_logical_trade_selection(account.id)
        st.session_state[_logical_trade_page_key(account.id)] = 1
    st.session_state[filter_key] = current_filter
    selected_keys_set = set(selected_keys)
    visible = [
        (trade, scores[trade.id])
        for trade in ordered
        if review_kind_to_filter_key.get(scores[trade.id].review_kind) in selected_keys_set
    ]
    visible_by_id = {trade.id: trade for trade, _ in visible}
    selected_logical_trade_ids = tuple(
        trade_id
        for trade_id in st.session_state.get(_logical_trade_selection_store_key(account.id), ())
        if trade_id in visible_by_id
    )
    st.session_state[_logical_trade_selection_store_key(account.id)] = selected_logical_trade_ids
    visible_trade_ids = tuple(visible_by_id)
    select_all_key = _logical_trade_select_all_key(account.id)
    st.session_state[select_all_key] = bool(visible_trade_ids) and len(selected_logical_trade_ids) == len(visible_trade_ids)
    selected_position_trade_ids = tuple(
        member.id
        for trade_id in selected_logical_trade_ids
        for member in visible_by_id[trade_id].members
    )
    selected_reviewable_count = sum(
        scores[trade_id].review_kind in {"auto_review", "needs_approval"}
        for trade_id in selected_logical_trade_ids
    )
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.checkbox(
            "Check all",
            key=select_all_key,
            disabled=not visible_trade_ids,
            on_change=_toggle_all_logical_trade_selection,
            args=(account.id, visible_trade_ids),
        )
        st.button(
            "Clear selection",
            key=f"clear-logical-trade-selection-{account.id}",
            icon=":material/clear:",
            disabled=not selected_logical_trade_ids,
            on_click=_clear_logical_trade_selection,
            args=(account.id,),
        )
        create = st.button(
            f"Group selected ({len(selected_logical_trade_ids)})",
            key=f"create-logical-trade-{account.id}",
            icon=":material/group_work:",
            type="primary",
            disabled=len(selected_logical_trade_ids) < 2,
            help="Combine every position from two or more selected logical trades into a new logical trade.",
        )
        bulk_selected = st.button(
            f"Quick review selected ({selected_reviewable_count})",
            key=f"bulk-quick-review-selected-{account.id}",
            icon=":material/done_all:",
            type="primary",
            disabled=selected_reviewable_count == 0,
            help="Review selected Awaiting approval or Requires review trades in one confirmed action.",
        )
        auto_reviewed_visible = [(trade, score) for trade, score in visible if score.review_kind == "auto_review"]
        bulk_approve = (
            st.button(
                tr("Approve all visible within-policy ({count})", count=len(auto_reviewed_visible)),
                key=f"bulk-approve-auto-review-{account.id}",
                icon=":material/done_all:",
                help="Approve every within-policy auto-review trade currently shown by this filter, one click for all of them.",
            )
            if "auto_reviewed" in selected_keys_set and auto_reviewed_visible
            else False
        )
    if bulk_approve:
        approved_count = 0
        skipped_count = 0
        with st.spinner(tr("Saving…")):
            for trade, score in auto_reviewed_visible:
                try:
                    repo.approve_auto_review(
                        account_id=account.id, trade_id=trade.id, risk_policy_id=score.auto_risk.policy_id,
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
        queue_toast(message)
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
            "selected_logical_trade_ids": selected_logical_trade_ids,
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
        if not selected_keys:
            st.info(tr("Select at least one review status filter above to see trades."))
        else:
            status_text = " / ".join(tr(filter_names[key]).casefold() for key in selected_keys)
            st.info(tr("No {status} trades for this account.", status=status_text))
    else:
        position_by_id = {trade.id: index for index, (trade, _) in enumerate(visible)}
        start = (current_page - 1) * REVIEW_PAGE_SIZE
        page_items = visible[start : start + REVIEW_PAGE_SIZE]
        with st.container(key="trade-review-table-header"):
            header = st.columns([0.5, 0.85, 1.35, 1.3, 0.75, 1.1, 0.85, 0.75, 1.0])
            for column, label in zip(
                header,
                ("Select", "Logical trade", "Trade", "Positions", "P&L", "Review", "Score", "Hard rules", "Actions"),
                strict=True,
            ):
                if label == "Score":
                    with column:
                        with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
                            st.caption(tr(label))
                            _render_help_popover(
                                "P = Psychology · R = Risk management · S = Trading system. Each trade keeps the rubric used when it was reviewed; the Monitor uses only Zone-aligned reviews and a different rolling calculation.",
                                "Classification below: first word = process quality (Good/Needs improvement/Bad), second word = P&L outcome (Win/Loss/Breakeven) — independent of each other.",
                            )
                else:
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
                        "Select this logical trade for Bulk Quick Review or to group it, with all of its positions, with other logical trades."
                    ),
                    on_change=_toggle_logical_trade_selection,
                    args=(account.id, trade.id, visible_trade_ids),
                )
                _mobile_field_label(logical_column, "Logical trade")
                logical_column.markdown(f"**LT-{trade.id}**")
                _mobile_field_label(trade_column, "Trade")
                trade_column.write(trade.display_label)
                direction = direction_tag(trade.direction)
                outcome = outcome_tag(trade.net_pnl)
                trade_column.badge(tr(direction.label), color=direction.color, icon=direction.icon)
                _mobile_field_label(positions_column, "Positions")
                positions_column.write(", ".join(f"#{position_id}" for position_id in trade.position_ids))
                _mobile_field_label(pnl_column, "P&L")
                pnl_column.write(format_currency(trade.net_pnl, account.account_currency))
                pnl_column.badge(tr(outcome.label), color=outcome.color, icon=outcome.icon)
                _mobile_field_label(review_column, "Review")
                review_column.write(tr(review))
                _mobile_field_label(score_column, "Score")
                score_column.markdown(f"**{_score_text(score.overall_score)}**")
                psychology_flag = " ⚠" if score.psychology_hard_block else ""
                risk_flag = " ⚠" if score.risk_hard_block else ""
                system_flag = " ⚠" if score.system_hard_block else ""
                score_column.caption(
                    f"P {_score_text(score.psychology_score)}{psychology_flag}  \n"
                    f"R {_score_text(score.risk_score)}{risk_flag}  \n"
                    f"S {_score_text(score.system_score)}{system_flag}"
                )
                if score.rubric_version is not None:
                    score_column.caption(_rubric_label(score.rubric_version))
                _mobile_field_label(process_column, "Hard rules")
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
                        else "Accept the automatic risk evidence in one click instead of a full Zone-aligned 12-criterion review."
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
                                    account_id=account.id, trade_id=trade.id, risk_policy_id=score.auto_risk.policy_id,
                                    risk_evidence_source=score.risk_evidence_source,
                                    risk_policy_state=score.risk_policy_state,
                                    actual_risk_amount=score.actual_risk_amount,
                                    criterion_grades=FrameworkService._automatic_review_grades(score.risk_policy_state),
                                )
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.session_state["post-trade-review-notice"] = "Automatic risk evidence approved."
                            queue_toast(tr("Automatic risk evidence approved."))
                            st.rerun()
                if trade.is_group and actions_column.button(
                    "Ungroup",
                    key=f"ungroup-logical-trade-{account.id}-{trade.id}",
                    type="tertiary",
                    icon=":material/group_off:",
                ):
                    _begin_logical_trade_disband(repo, account, trade.id)
                opened_at = _reporting_time(repo, trade.entry_time, trade.server_utc_offset_minutes)
                closed_at = _reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes)
                execution_columns = st.columns([1.25, 1, 1.25, 1, 0.8, 0.8])
                execution_values = (
                    ("Opened", opened_at),
                    ("Entry price", _format_execution_number(trade.entry_price)),
                    ("Closed", closed_at),
                    ("Exit price", _format_execution_number(trade.exit_price)),
                    ("Duration", _format_trade_duration(trade.entry_time, trade.exit_time)),
                    ("Size", f"{_format_execution_number(trade.volume)} {tr('lots')}"),
                )
                for detail_column, (label, value) in zip(execution_columns, execution_values, strict=True):
                    detail_column.caption(tr(label))
                    detail_column.markdown(f"**{value}**")
                summary = (
                    f"{trade.symbol} {trade.direction} · {_auto_risk_label(score)} "
                    f"· {tr(score.classification or 'Unclassified')}"
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
            st.dialog(tr("Manage logical-trade positions"), width="large", on_dismiss=_clear_group_dialog)(_render_logical_trade_group_dialog)(
                repo,
                account,
                group,
                tuple(group_editor.get("selected_position_trade_ids", ())),
                tuple(group_editor.get("selected_logical_trade_ids", ())),
            )
        else:
            _clear_group_dialog()
    if st.session_state.get(_bulk_quick_review_key(account.id)) is not None:
        st.dialog(tr("Quick review selected trades"), width="large", on_dismiss=_dismiss_bulk_quick_review)(_render_bulk_quick_review_dialog)(repo, account, trades, scores)
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
        st.toast(tr("A queued trade could no longer be reviewed (its logical trade changed) and was skipped."), icon=":material/info:")
    if item is None:
        _clear_review_dialog()
        return
    st.dialog(tr("Post-trade assessment"), width="large", on_dismiss=_clear_review_dialog)(_render_post_trade_review_dialog)(repo, account, item[0], item[1], profiles)


def _render_monitor(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    settings = repo.get_journal_settings()
    local_zone = browser_timezone() if settings.reporting_time_basis == "local" else None
    service = FrameworkService(repo, local_zone=local_zone)
    st.markdown(tr("#### Monitoring"))
    _render_risk_configuration_notice(service, account.id)
    controls, scope_note = st.columns((2, 3))
    with controls:
        window = st.slider(tr("Rolling sample"), min_value=10, max_value=100, value=20, step=5, key=f"framework-window-{account.id}")
        period = st.segmented_control(tr("Analysis period"), ["This month", "Last 90 days", "All time", "Custom"], format_func=tr, default="Last 90 days", required=True, key=f"framework-analysis-period-{account.id}")
    today = service.today(account.id)
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
            st.info(tr("Choose a start and end date for Monitor analysis."))
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
    _render_rubric_sample_caption(scores, int(window))
    _render_framework_focus(repo, account, service, scores)
    readiness_value, readiness_delta, readiness_color = _readiness_metric(readiness)
    st.metric(
        tr("Overall readiness"), readiness_value, tr(readiness_delta),
        delta_color=readiness_color, delta_arrow="off", border=True,
    )
    st.caption(readiness.detail)
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.metric(tr("Risk checks"), f"{coverage.approved}/{coverage.total}", tr("approved evidence"), border=True)
        st.metric("Risk pending", str(coverage.pending), border=True)
        st.metric("Over policy", str(coverage.over_policy), border=True)
        st.metric("Risk unavailable", str(coverage.unavailable), border=True)
        _render_help_popover("Approved Quick Risk Checks and Manual Reviews both feed pillar scores, readiness, and roadmap gates.")
    _render_score_cards(scores, account)
    _render_pillar_radar(scores)
    component_rows = [
        {tr("Pillar"): tr(PILLAR_NAMES[score.pillar]), tr("Metric"): tr(name), tr("Score"): _score_text(value), tr("Scope"): tr(score.scope)}
        for score in scores for name, value in score.component_scores
    ]
    if component_rows:
        present_names = {name for score in scores for name, _ in score.component_scores}
        with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
            st.markdown("##### What drives the current scores")
            with st.popover(tr("What do these mean?"), icon=":material/help:", width="content"):
                for name in COMPONENT_DEFINITIONS:
                    if name in present_names:
                        st.caption(f"**{tr(name)}** — {tr(COMPONENT_DEFINITIONS[name])}")
        st.dataframe(pd.DataFrame(component_rows), hide_index=True, width="stretch")
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
        with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
            st.markdown("##### Score trend")
            _render_help_popover("Zone-aligned series follow the selected rolling window. Legacy series preserve each trade's original score, and the rubrics remain separate so unlike criteria are never silently blended.")
        records = []
        for closed, psychology, risk, system, rubric_version in trend:
            rubric = _rubric_label(rubric_version)
            records.append({
                "Closed": closed,
                f"Psychology · {rubric}": None if psychology is None else float(psychology),
                f"Risk management · {rubric}": None if risk is None else float(risk),
                f"Trading system · {rubric}": None if system is None else float(system),
            })
        frame = pd.DataFrame(records).groupby("Closed", as_index=True).first()
        st.line_chart(frame, width="stretch")
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
        st.metric("Max drawdown", "—" if snapshot.max_drawdown_percent is None else format_percent(snapshot.max_drawdown_percent), border=True)
        st.metric("Loss streak", "—" if snapshot.consecutive_losses is None else str(snapshot.consecutive_losses), border=True)
    if snapshot.configured:
        current_drawdown = "—" if snapshot.current_drawdown_percent is None else format_percent(snapshot.current_drawdown_percent)
        st.caption(tr(
            "Current drawdown: {current_drawdown}. Max drawdown resets {drawdown_period}; the losing streak resets {streak_period}.",
            current_drawdown=current_drawdown,
            drawdown_period=_reset_period_label(snapshot.drawdown_reset_period).lower(),
            streak_period=_reset_period_label(snapshot.loss_streak_reset_period).lower(),
        ))
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


def _render_framework_focus_action_dialog(
    repo: SQLiteJournalRepository,
    *,
    focus_id: int,
    action_text: str,
) -> None:
    st.caption(tr("Tailor the next-trade action without losing sight of the current coaching target."))
    with st.form(f"edit-framework-focus-{focus_id}", border=False):
        action = st.text_area(tr("Tailor the next-trade action"), value=action_text)
        with st.container(horizontal=True, horizontal_alignment="right"):
            cancel = st.form_submit_button(tr("Cancel"))
            save = st.form_submit_button(tr("Save action"), type="primary", icon=":material/save:")
    if cancel:
        st.rerun()
    if not save:
        return
    try:
        repo.update_framework_focus_action(focus_id=focus_id, action_text=action)
    except ValueError as error:
        st.error(str(error))
    else:
        queue_toast(tr("Coaching action saved."))
        st.rerun()


def _render_framework_focus_resolution_dialog(
    repo: SQLiteJournalRepository,
    *,
    focus_id: int,
) -> None:
    st.caption(tr("Record the outcome and the lesson you will carry into the next focus."))
    with st.form(f"resolve-framework-focus-{focus_id}"):
        outcome = st.segmented_control(
            tr("Focus outcome"),
            ["completed", "abandoned"],
            format_func=tr,
            default="completed",
            required=True,
        )
        note = st.text_area(
            tr("Focus reflection"),
            placeholder=tr("What changed, and what will you carry forward?"),
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            cancel = st.form_submit_button(tr("Cancel"))
            resolve = st.form_submit_button(
                tr("Resolve focus"),
                type="primary",
                icon=":material/task_alt:",
            )
    if cancel:
        st.rerun()
    if not resolve:
        return
    try:
        repo.resolve_framework_focus(
            focus_id=focus_id,
            outcome=outcome,
            resolution_note=note,
        )
    except ValueError as error:
        st.error(str(error))
    else:
        queue_toast(tr("Framework focus resolved."))
        st.rerun()


def _render_framework_focus(repo: SQLiteJournalRepository, account: AccountListItem, service: FrameworkService, scores: tuple[PillarScore, ...], *, compact: bool = False, show_heading: bool = True) -> None:
    service.ensure_coaching_focus(account.id)
    focus, progress = service.focus_progress(account.id)
    if show_heading:
        heading = tr("🎯 Current coaching focus") if compact else tr("Coaching focus")
        st.markdown(f"##### {heading}")

    if focus is not None and progress is not None:
        with st.container(border=True):
            kind = {"manual_evidence": "Reviewed evidence", "component": "Pillar component", "criterion": "Criterion", "violation": "Issue"}[focus.metric_kind]
            st.markdown(f"**{tr(PILLAR_NAMES[focus.pillar])} · {tr(kind)}**")
            st.write(tr(focus.action_text))
            st.caption(f"{tr('Why now:')} {tr(focus.coach_reason or focus.hypothesis)}")
            opened_relative = format_relative_time_localized(format_relative_time(datetime.fromisoformat(focus.created_at)))
            st.caption(tr("Focus opened {relative} · progress advances only as you review trades, not automatically each day.", relative=opened_relative))
            current = _focus_metric_text(progress.current_value, focus.metric_kind)
            baseline = _focus_metric_text(focus.baseline_value, focus.metric_kind)
            target = _focus_metric_text(focus.target_value, focus.metric_kind)
            display_completed = min(progress.reviews_completed, progress.target_reviews)
            metric_delta = tr("Target reached") if progress.reviews_completed >= progress.target_reviews else f"{tr('Current metric:')} {current}"
            st.metric(tr("Reviewed trades collected"), f"{display_completed}/{progress.target_reviews}", metric_delta, border=True)
            st.caption(f"{tr('Baseline:')} {baseline} · {tr('Target:')} {target}")
            with st.container(horizontal=True, horizontal_alignment="right"):
                edit_action = st.button(
                    tr("Edit action"),
                    key=f"open-edit-framework-focus-{focus.id}",
                    icon=":material/edit:",
                )
                resolve_focus = progress.ready_to_evaluate and st.button(
                    tr("Resolve focus"),
                    key=f"open-resolve-framework-focus-{focus.id}",
                    type="primary",
                    icon=":material/task_alt:",
                )
            if edit_action:
                st.dialog(tr("Edit coaching action"), icon=":material/edit:")(_render_framework_focus_action_dialog)(
                    repo,
                    focus_id=focus.id,
                    action_text=focus.action_text,
                )
            elif resolve_focus:
                st.dialog(tr("Resolve coaching focus"), icon=":material/task_alt:")(_render_framework_focus_resolution_dialog)(repo, focus_id=focus.id)
            history = [item for item in repo.list_framework_focuses(account.id) if item.status != "active"]
            if history and not compact:
                with st.expander(tr("Coaching history")):
                    for item in history[:5]:
                        st.markdown(f"**{item.status.capitalize()} · {PILLAR_NAMES[item.pillar]}** — {item.action_text}")
                        if item.resolution_note:
                            st.caption(item.resolution_note)
        return
    pending_reason = service.pending_coaching_reason(account.id)
    if pending_reason is not None:
        st.info(tr(pending_reason))
    else:
        st.success(tr("On track: no coaching intervention is required from the current reviewed evidence."))


def render_dashboard_coaching_focus(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    """Collapsible coaching nudge fixed in the Dashboard's top-right corner, always visible above scrolled content."""
    theme_type = st.context.theme.type or "light"
    background = st.get_option(f"theme.{theme_type}.secondaryBackgroundColor") or ("#141a18" if theme_type == "dark" else "#eeeee7")
    st.markdown(
        f"""
        <style>
        div.st-key-dashboard-coaching-focus {{
            position: fixed;
            top: 4.75rem;
            right: 1.5rem;
            z-index: 998;
            width: min(24rem, 90vw);
            max-height: 80vh;
            overflow-y: auto;
            background-color: {background};
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
            border-radius: 0.5rem;
        }}
        @media (max-width: 640px) {{
            /* Anchor to the bottom on phones instead: a top-right offset this narrow
               lands on the logo/title row rather than clearing it. The extra bottom
               offset leaves room for the global alert badge, which also docks in
               the bottom-right corner on phones (see global_alert_bubble.py). */
            div.st-key-dashboard-coaching-focus {{
                top: auto;
                bottom: 4.5rem;
                right: 1rem;
                left: 1rem;
                width: auto;
                max-height: 55vh;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    service = FrameworkService(repo)
    scores = service.pillar_scores(account.id)
    with st.expander(tr("🎯 Current coaching focus"), expanded=False, key="dashboard-coaching-focus"):
        _render_framework_focus(repo, account, service, scores, compact=True, show_heading=False)


def _render_period_reviews(repo: SQLiteJournalRepository, account: AccountListItem, service: FrameworkService) -> None:
    st.markdown(f"##### {tr('Weekly and monthly reviews')}")
    st.caption(
        tr(
            "Track the active calendar periods now, then reflect after each period closes. Period reviews assess process evidence; they are not profit-based pass/fail tests."
        )
    )
    basis = repo.get_journal_settings().reporting_time_basis
    basis_label = {
        "server": tr("MT5 server time"),
        "utc": "UTC",
        "local": tr("local computer time"),
    }[basis]
    st.info(
        tr(
            "Review calendar: {basis}. Calendar today is {date}; a period becomes reviewable on the next calendar day.",
            basis=basis_label,
            date=service.today(account.id).isoformat(),
        ),
        icon=":material/calendar_today:",
    )
    if basis != "local":
        st.page_link(
            "app_pages/settings.py",
            label=tr("Use this device's local calendar in Settings"),
            icon=":material/settings:",
        )

    st.markdown(f"###### {tr('Ongoing periods')}")
    ongoing_statuses = [service.ongoing_period_status(account.id, cadence) for cadence in ("weekly", "monthly")]
    with st.container(horizontal=True, gap="small"):
        for status in ongoing_statuses:
            pending = status.closed_trades - status.reviewed_trades
            with st.container(border=True):
                st.markdown(f"**{tr(f'Ongoing {status.cadence}')}**")
                st.caption(f"{status.period_start} to {status.period_end}")
                if status.closed_trades:
                    st.write(
                        tr(
                            "{reviewed} of {closed} closed trades reviewed with the current rubric · {pending} pending",
                            reviewed=format_count(status.reviewed_trades),
                            closed=format_count(status.closed_trades),
                            pending=format_count(pending),
                        )
                    )
                else:
                    st.write(tr("No closed trades yet"))
                st.caption(tr("Review opens {date}.", date=status.review_opens_on))

    st.markdown(f"###### {tr('Latest completed periods')}")
    statuses = [service.period_review_status(account.id, cadence) for cadence in ("weekly", "monthly")]
    with st.container(horizontal=True, gap="small"):
        for status in statuses:
            if status.disposition == "skipped":
                status_label = tr("Skipped")
            elif status.disposition == "reviewed":
                status_label = tr("Reviewed")
            elif status.closed_trades == 0:
                status_label = tr("No activity")
            elif status.reviewed_trades == 0:
                status_label = tr("Pending review")
            elif status.due:
                status_label = tr("Due")
            else:
                status_label = tr("Up to date")
            with st.container(border=True):
                st.metric(tr(f"Last completed {status.cadence}"), status_label)
                st.caption(f"{status.period_start} to {status.period_end}")
                st.caption(
                    tr(
                        "{reviewed} current-rubric reviewed · {closed} closed",
                        reviewed=format_count(status.reviewed_trades),
                        closed=format_count(status.closed_trades),
                    )
                )

    backlog = sorted(
        (
            status
            for cadence in ("weekly", "monthly")
            for status in service.period_review_backlog(account.id, cadence)
        ),
        key=lambda status: (status.period_end, status.cadence),
    )
    if backlog:
        st.markdown(f"###### {tr('Past periods requiring attention')}")
        st.caption(
            tr(
                "Every completed period containing trades stays unreviewed until you review or explicitly skip it. Choosing a newer period never hides an older one."
            )
        )
        attention = pd.DataFrame(
            [
                {
                    tr("Cadence"): tr(f"{status.cadence.capitalize()} review"),
                    tr("Period"): f"{status.period_start} to {status.period_end}",
                    tr("Status"): tr("Review due" if status.due else "Review trades first"),
                    tr("Reviewed"): status.reviewed_trades,
                    tr("Closed"): status.closed_trades,
                    tr("Pending"): status.closed_trades - status.reviewed_trades,
                }
                for status in backlog
            ]
        )
        st.dataframe(attention, hide_index=True, width="stretch")

    if backlog:
        selected = backlog[0]
        if len(backlog) > 1:
            selected = st.selectbox(
                tr("Choose a period"),
                backlog,
                format_func=lambda status: tr(
                    "{cadence} · {start} to {end}",
                    cadence=tr(f"{status.cadence.capitalize()} review"),
                    start=status.period_start,
                    end=status.period_end,
                ),
                key=f"period-review-selection-{account.id}",
            )
        if selected.due:
            with st.form(f"period-review-{account.id}-{selected.cadence}-{selected.period_end}", border=True):
                st.markdown(f"**{tr(f'{selected.cadence.capitalize()} review due')}**")
                st.caption(tr("Save the {cadence} reflection for {start} to {end}.", cadence=tr(selected.cadence), start=selected.period_start, end=selected.period_end))
                note = st.text_area("Review note", placeholder="What pattern did the data reveal?")
                action = st.text_area("One priority corrective action", placeholder="Choose one focused action for the next period.")
                submitted = st.form_submit_button("Save period review", type="primary")
                if submitted:
                    try:
                        with st.spinner(tr("Saving…")):
                            service.save_period_review(
                                account_id=account.id,
                                cadence=selected.cadence,
                                period_start=selected.period_start,
                                period_end=selected.period_end,
                                review_note=note,
                                priority_action=action,
                            )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        queue_toast(tr("Period review saved."))
                        st.rerun()
        else:
            st.warning(tr("Review every closed trade in this period before saving its reflection, or skip the period with a reason."))
        with st.form(f"skip-period-{account.id}-{selected.cadence}-{selected.period_end}", border=False):
            skip_reason = st.text_input(tr("Skip reason"), placeholder=tr("Why are you intentionally not completing this period review?"))
            skipped = st.form_submit_button(tr("Skip period"))
            if skipped:
                try:
                    service.skip_period_review(
                        account_id=account.id,
                        cadence=selected.cadence,
                        period_start=selected.period_start,
                        period_end=selected.period_end,
                        reason=skip_reason,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    queue_toast(tr("Period skipped."))
                    st.rerun()

    reviews = repo.list_framework_period_reviews(account.id)
    if reviews:
        st.markdown(f"###### {tr('Period history')}")
        history = pd.DataFrame(
            [
                {
                    tr("Cadence"): tr(f"{review.cadence.capitalize()} review"),
                    tr("Period"): f"{review.period_start} to {review.period_end}",
                    tr("Status"): tr(review.status.capitalize()),
                    tr("Rubric"): _rubric_label(review.rubric_version),
                    tr("Psychology"): _score_text(review.psychology_score),
                    tr("Risk management"): _score_text(review.risk_score),
                    tr("Trading system"): _score_text(review.system_score),
                    tr("Readiness"): _score_text(review.readiness_score),
                    tr("Recurring issues"): ", ".join(_review_history_code_label(code) for code in review.recurring_issues) or "—",
                    tr("Alerts"): ", ".join(_review_history_code_label(code, alert=True) for code in review.alert_codes) or "—",
                    tr("Review note"): review.review_note,
                    tr("Priority action"): review.priority_action,
                    tr("Saved"): review.created_at,
                }
                for review in reviews
            ]
        )
        st.dataframe(history, hide_index=True, width="stretch")
    else:
        st.caption(tr("No weekly or monthly reviews have been saved yet."))


def _render_roadmap(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    statuses = {item.pillar: item for item in service.roadmap_status(account.id)}
    legacy_psychology_evidence = service.legacy_psychology_roadmap_evidence(account.id)
    with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
        st.markdown("#### Readiness roadmap")
        _render_help_popover("Define, Test, Execute, Measure, then Optimize. Most steps are detected automatically as you use the journal — only a few still need your own note.")

    pillar_tabs = st.tabs([tr(name) for name in PILLAR_NAMES.values()])
    for (pillar, name), tab in zip(PILLAR_NAMES.items(), pillar_tabs, strict=True):
        status = statuses[pillar]
        with tab:
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
                                queue_toast(completed_message)
                                st.rerun()

            history_items = [item for item in status.items if item.completed]
            if history_items:
                with st.expander(tr("Completed evidence"), icon=":material/history:"):
                    for item in sorted(history_items, key=lambda entry: (entry.level, entry.item_key)):
                        tag = tr("Auto") if item.is_auto else tr("Manual")
                        detail = f" — {item.evidence_summary}" if item.evidence_summary else ""
                        st.markdown(f"- {tr('Level')} {item.level} · {tag}: {tr(item.label)}{detail}")
            if pillar == "psychology" and legacy_psychology_evidence:
                with st.expander(tr("Legacy Psychology roadmap evidence"), icon=":material/archive:"):
                    st.caption(
                        tr(
                            "These pre-v2 notes are retained for audit history. They do not satisfy the Zone-aligned roadmap because the underlying Psychology evidence changed."
                        )
                    )
                    for item in sorted(legacy_psychology_evidence, key=lambda entry: (entry.level, entry.item_key)):
                        state = tr("Completed") if item.completed else tr("Not completed")
                        detail = f" — {item.evidence_note}" if item.evidence_note else ""
                        label = LEGACY_PSYCHOLOGY_ROADMAP_ITEMS[item.item_key]
                        st.markdown(f"- {tr('Level')} {item.level} · {state}: {tr(label)}{detail}")
def _render_risk_policy(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    policy = repo.get_active_risk_policy(account.id)
    funded = repo.get_account_funded_capital(account.id)
    st.markdown("#### Account risk policy")
    st.caption("The policy defines reporting 1R and safety limits. It monitors closed logical trades assembled from MT5 positions and never controls MT5.")
    if funded is None:
        st.warning(tr("Set funded capital in Settings → Account & risk before saving a Risk policy."))
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
        first, second = st.columns(2)
        reset_period_options = {tr(label): value for label, value in RESET_PERIOD_LABELS.items()}
        drawdown_reset_label = first.segmented_control(
            tr("Drawdown reset"),
            list(reset_period_options),
            default=next(label for label, value in reset_period_options.items() if value == (policy.drawdown_reset_period if policy else "daily")),
            required=True,
        )
        streak_reset_label = second.segmented_control(
            tr("Losing-streak reset"),
            list(reset_period_options),
            default=next(label for label, value in reset_period_options.items() if value == (policy.loss_streak_reset_period if policy else "daily")),
            required=True,
        )
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
                    drawdown_reset_period=reset_period_options[drawdown_reset_label],
                    loss_streak_reset_period=reset_period_options[streak_reset_label],
                )
        except ValueError as error:
            st.error(str(error))
        else:
            queue_toast(tr("Risk policy saved as a new version."))
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
            queue_toast(tr("Review rules saved."))
            st.rerun()
