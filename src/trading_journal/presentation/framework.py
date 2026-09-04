"""Native Streamlit presentation for the greenfield three-pillar framework."""

from __future__ import annotations

from collections import Counter
from collections.abc import MutableMapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape

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
    ReadinessAssessment,
    RiskSnapshot,
    TradeProcessScore,
)
from trading_journal.application.display_time import format_relative_time
from trading_journal.application.reporting_time import reporting_datetime
from trading_journal.domain.review_taxonomy import REVIEW_MISTAKE_CODES, REVIEW_MISTAKES_BY_PILLAR
from trading_journal.infrastructure.sqlite_repository import (
    ASSESSMENT_CRITERIA,
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
PILLAR_ACCENT_COLORS = {"Psychology": "blue", "Risk management": "orange", "Trading system": "violet"}
CRITERION_LABELS = {
    "edge_execution": "Edge execution",
    "risk_acceptance": "Risk acceptance",
    "probability_mindset": "Probability mindset",
    "outcome_independence": "Outcome independence and reset",
    "policy_adherence": "Risk-policy adherence",
    "position_size_accuracy": "Position-size accuracy",
    "stop_discipline": "Stop discipline",
    "exposure_limit_compliance": "Exposure & limit compliance",
    "setup_validity": "Setup validity",
    "context_alignment": "Context alignment",
    "entry_fidelity": "Entry fidelity",
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
MISTAKE_CATEGORY_PREFIXES = {
    code: {
        "psychology": "P",
        "risk": "R",
        "system": "S",
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


def _render_help_popover(*captions: str) -> None:
    """A compact on-demand help trigger, keeping page content focused."""
    with st.popover("?", width="content"):
        for text in captions:
            st.caption(text)


def _score_text(value: str | None) -> str:
    return "—" if value is None else format_score(value)


def _prs_summary(score: TradeProcessScore) -> str:
    """Condense the Psychology/Risk/System breakdown to bare numbers, in that fixed
    order (documented once in the Score column's help popover), next to the overall
    score badge - e.g. overall badge "56%" beside this caption "(50-70⚠-50)"."""

    def part(value: str | None, blocked: bool) -> str:
        text = _score_text(value)
        number = text[:-1] if text.endswith("%") else text
        return f"{number}⚠" if blocked else number

    fields = (
        (score.psychology_score, score.psychology_hard_block),
        (score.risk_score, score.risk_hard_block),
        (score.system_score, score.system_hard_block),
    )
    return "(" + "-".join(part(value, blocked) for value, blocked in fields) + ")"


_SCORE_BADGE_COLOR = {"good": "green", "needs_improvement": "orange", "bad": "red"}


def _rubric_label(rubric_version: str | None) -> str:
    return tr("Zone-aligned 12-criterion")


def _render_rubric_sample_caption(scores: tuple[PillarScore, ...], window: int) -> None:
    reviewed = min((score.sample_size for score in scores), default=0)
    st.caption(
        tr(
            "Zone-aligned sample: {reviewed}/{window} reviewed trades",
            reviewed=reviewed,
            window=window,
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


def _compact_execution_window(entry: str, exit_: str, duration: str) -> str:
    """Keep a logical trade's time range legible inside a narrow summary card."""
    entry_date, entry_time = entry.split(" ", 1)
    exit_date, exit_time = exit_.split(" ", 1)
    if entry_date == exit_date:
        return f"{entry_date} · {entry_time[:5]} → {exit_time[:5]} · {duration}"
    return f"{entry_date} {entry_time[:5]} → {exit_date} {exit_time[:5]} · {duration}"


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


def _format_execution_number(value: str, *, reference: str | None = None) -> str:
    """Preserve imported precision while adding separators for execution facts.

    A grouped logical trade's entry/exit price is a notional-weighted average across
    its member positions, which can carry dozens of repeating digits (e.g.
    4,466.67266666666666666667). Round to the decimal precision the broker actually
    quotes this symbol at - taken from a raw member position's price via `reference`
    - instead of showing the raw division result or an arbitrary fixed precision.
    """
    precision_source = reference if reference is not None else value
    decimals = len(precision_source.split(".", 1)[1]) if "." in precision_source else 0
    quantum = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    text = f"{Decimal(value).quantize(quantum):,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


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


def _build_pillar_radar_figure(scores: tuple[PillarScore, ...]) -> go.Figure:
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
    return figure


def _render_pillar_radar(scores: tuple[PillarScore, ...]) -> None:
    blocked = [score.hard_block for score in scores]
    capped = [score.status == "caution" and not score.hard_block for score in scores]
    figure = _build_pillar_radar_figure(scores)
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    sample_line = "  ·  ".join(f"{tr(PILLAR_NAMES[score.pillar])} {score.sample_size}" for score in scores)
    with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
        st.caption(tr("Sample size: {sample_line}", sample_line=sample_line))
        if any(blocked) or any(capped) or any(score.score is None for score in scores):
            with st.popover("?", width="content"):
                if any(blocked) and any(capped):
                    st.caption(tr("Pillars marked ✕ in red have an active hard-rule failure; pillars marked ◆ in amber are capped by repeated critical violations — neither score reflects readiness."))
                elif any(blocked):
                    st.caption(tr("Pillars marked ✕ in red have an active hard-rule failure — their score does not reflect readiness."))
                elif any(capped):
                    st.caption(tr("Pillars marked ◆ in amber are capped at 59 by repeated critical violations — see the detail below the score card."))
                elif any(score.score is None for score in scores):
                    st.caption(tr("Pillars without a scored sample yet show as 0 on this chart."))


def _pillar_monitor_status(score: PillarScore) -> tuple[str, str]:
    if score.hard_block:
        return tr("FAIL"), "#c73545"
    if score.score is None:
        return tr("Incomplete"), "#7a828e"
    if score.status == "incomplete":
        return tr("Early estimate"), "#7a828e"
    if score.status == "caution":
        return tr("Caution"), "#a65f00"
    return tr(score.status.capitalize()), "#0e9163"


def _render_framework_stat_grid(items: list[tuple[str, str, str, str]]) -> None:
    cells = "".join(
        '<div class="dashboard-stat">'
        f'<div class="dashboard-stat-label">{escape(label)}</div>'
        f'<div class="dashboard-stat-value dashboard-stat-tone-{tone}">{escape(value)}</div>'
        f'<div class="dashboard-stat-note">{escape(note)}</div>'
        "</div>"
        for label, value, note, tone in items
    )
    st.markdown(f'<div class="dashboard-stat-grid dashboard-framework-stats">{cells}</div>', unsafe_allow_html=True)


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
    readiness_value, readiness_delta, readiness_color = _readiness_metric(readiness)
    state_value, state_delta, state_color = _risk_state_metric(snapshot)
    daily_value, daily_delta, daily_color = _daily_r_metric(snapshot.daily_r)
    drawdown_value, drawdown_delta, drawdown_color = _drawdown_metric(snapshot.max_drawdown_percent)
    tone_by_delta_color = {"green": "positive", "red": "negative", "orange": "warning", "gray": "neutral"}
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
            st.markdown(tr("#### Process & risk"))
            _render_help_popover(
                tr("Readiness uses the latest 20 completed reviews; incomplete evidence remains incomplete."),
                tr("Psychology, Risk management, and Trading system are scored independently."),
                tr("Outcome profitability does not increase readiness. Risk status follows the active account policy and its reset rules."),
            )
        st.caption(tr("Fixed 20-trade process window · outcome performance does not determine readiness."))
        _render_risk_configuration_notice(service, account.id)
        process_risk_columns = st.container(key="dashboard-process-risk-columns")
        stats, pillars = process_risk_columns.columns([1, 1.45], gap="large")
        with stats:
            _render_framework_stat_grid(
                [
                    (
                        tr("Overall readiness"),
                        "—" if readiness_value is None else str(readiness_value),
                        tr(readiness_delta),
                        tone_by_delta_color[readiness_color],
                    ),
                    (
                        tr("Risk state"),
                        str(state_value),
                        tr(state_delta),
                        tone_by_delta_color[state_color],
                    ),
                    (
                        tr("Today"),
                        "—" if daily_value is None else str(daily_value),
                        tr(daily_delta)
                        + ("" if policy is None else f" · {format_exposure_r(policy.daily_loss_limit_r)} {tr('limit')}"),
                        tone_by_delta_color[daily_color],
                    ),
                    (
                        tr("Max drawdown"),
                        "—" if drawdown_value is None else str(drawdown_value),
                        tr(drawdown_delta)
                        + (
                            ""
                            if not snapshot.configured
                            else f" · {tr('Resets {period}', period=_reset_period_label(snapshot.drawdown_reset_period).lower())}"
                        ),
                        tone_by_delta_color[drawdown_color],
                    ),
                ]
            )
            st.caption(tr(readiness.detail))
        with pillars:
            _render_pillar_radar(scores)
        _render_rubric_sample_caption(scores, 20)
        non_ready_scores = [score for score in scores if score.status != "ready"]
        if non_ready_scores:
            with st.popover(tr("Pillar score details"), icon=":material/help:", width="content"):
                for score in non_ready_scores:
                    status, _ = _pillar_monitor_status(score)
                    st.markdown(f"**{tr(PILLAR_NAMES[score.pillar])} · {status}**")
                    st.caption(tr(score.detail))
                    st.caption(_score_scope_label(score, account))


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
    trade_id = st.session_state.pop("post-trade-review-trade-id", None)
    st.session_state.pop("post-trade-review-queue", None)
    if isinstance(trade_id, int):
        _clear_assessment_draft(trade_id)


def _clear_assessment_draft(
    trade_id: int,
    state: MutableMapping[str, object] | None = None,
) -> None:
    target = st.session_state if state is None else state
    prefix = f"assessment-{trade_id}-"
    for key in [key for key in target if key.startswith(prefix)]:
        target.pop(key, None)


def _advance_review_queue(queue: Sequence[int]) -> tuple[int | None, tuple[int, ...]]:
    """Pop the next trade id off a review queue, leaving the remainder."""

    if not queue:
        return None, ()
    return queue[0], tuple(queue[1:])


def _clear_group_dialog() -> None:
    st.session_state.pop("logical-trade-group-editor", None)
    st.session_state.pop("logical-trade-regroup-confirmation", None)


_DISBAND_REVIEW_RETURN_KEY = "logical-trade-disband-review-return"


def _snapshot_review_before_disband(account_id: int, trade_id: int, queue: Sequence[int]) -> None:
    prefix = f"assessment-{trade_id}-"
    st.session_state[_DISBAND_REVIEW_RETURN_KEY] = {
        "account_id": account_id,
        "trade_id": trade_id,
        "queue": tuple(queue),
        "draft": {
            key: st.session_state[key]
            for key in tuple(st.session_state)
            if key.startswith(prefix)
        },
    }


def _restore_review_after_disband_cancel() -> None:
    saved = st.session_state.pop(_DISBAND_REVIEW_RETURN_KEY, None)
    _clear_group_dialog()
    if not isinstance(saved, dict):
        return
    st.session_state["post-trade-review-trade-id"] = saved["trade_id"]
    st.session_state["post-trade-review-queue"] = tuple(saved.get("queue", ()))
    for key, value in saved.get("draft", {}).items():
        st.session_state[key] = value


def _finish_review_after_disband() -> None:
    saved = st.session_state.pop(_DISBAND_REVIEW_RETURN_KEY, None)
    if not isinstance(saved, dict):
        return
    trade_id = saved["trade_id"]
    _clear_assessment_draft(trade_id)
    selected, remaining = _advance_review_queue(tuple(saved.get("queue", ())))
    st.session_state["post-trade-review-trade-id"] = selected
    st.session_state["post-trade-review-queue"] = remaining


def _dismiss_group_dialog() -> None:
    if st.session_state.get(_DISBAND_REVIEW_RETURN_KEY) is not None:
        _restore_review_after_disband_cancel()
    else:
        _clear_group_dialog()


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


def _render_imported_execution(repo: SQLiteJournalRepository, account: AccountListItem, trade, score: TradeProcessScore) -> bool:  # type: ignore[no-untyped-def]
    direction = direction_tag(trade.direction)
    outcome = outcome_tag(trade.net_pnl)
    risk_source, separator, risk_state = _auto_risk_label(score).partition(" · ")
    risk_color = {
        "within_policy": "green",
        "over_policy": "red",
        "unavailable": "gray",
    }.get(score.risk_policy_state, "gray")
    summary_column, positions_column = (
        st.columns([0.9, 1.6], gap="small", vertical_alignment="top")
        if trade.is_group
        else (st.container(), None)
    )
    disband_requested = False
    with summary_column:
        with st.container(border=True):
            st.markdown(f"**{trade.symbol}**")
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.badge(tr(direction.label), color=direction.color, icon=direction.icon)
                st.badge(format_currency(trade.net_pnl, account.account_currency), color=outcome.color)
                if score.overall_score is not None:
                    st.badge(
                        f"{tr('Trade score')}: {_score_text(score.overall_score)}",
                        color=_SCORE_BADGE_COLOR.get(score.quality_status, "gray"),
                    )
                st.badge(risk_state if separator else risk_source, color=risk_color)
            if separator:
                st.caption(f"{tr('Risk evidence')} · {risk_source}")
            entry_time = _reporting_time(repo, trade.entry_time, trade.server_utc_offset_minutes)
            exit_time = _reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes)
            st.caption(
                _compact_execution_window(
                    entry_time,
                    exit_time,
                    _format_trade_duration(trade.entry_time, trade.exit_time),
                )
            )
            risk_detail = _risk_evidence_detail(score)
            if risk_detail != "No usable automatic risk source is available.":
                st.caption(tr(risk_detail))
    if positions_column is not None:
        with positions_column:
            with st.container(border=True):
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.markdown(f"**{tr('Member positions ({count})', count=trade.position_count)}**")
                    disband_requested = st.form_submit_button(
                        tr("Disband"),
                        icon=":material/group_off:",
                    )
                pnl_column = f"P&L ({account.account_currency})"
                member_frame = pd.DataFrame(
                    [
                        {
                            tr("Position"): f"#{member.position_id or '—'}",
                            tr("Opened"): _reporting_time(repo, member.entry_time, member.server_utc_offset_minutes),
                            tr("Closed"): _reporting_time(repo, member.exit_time, member.server_utc_offset_minutes),
                            pnl_column: format_currency(member.net_pnl, account.account_currency),
                        }
                        for member in trade.members
                    ]
                )
                st.dataframe(
                    member_frame.style.map(_pnl_cell_style, subset=[pnl_column]),
                    hide_index=True,
                    width="stretch",
                )
    return disband_requested


def _grade_control(label: str, *, key: str, help_text: str | None = None) -> str | None:
    choice = st.segmented_control(
        label,
        GRADE_OPTIONS,
        format_func=tr,
        key=key,
        help=None if help_text is None else tr(help_text),
        width="stretch",
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


def _initialize_assessment_grades(
    trade_id: int,
    existing: dict[str, str] | None,
    risk_policy_state: str,
    state: MutableMapping[str, object] | None = None,
) -> None:
    """Initialize every grade before summaries or keyed widgets read the draft."""

    target = st.session_state if state is None else state
    for criterion in ASSESSMENT_CRITERIA:
        key = f"assessment-{trade_id}-{criterion}"
        if key in target:
            continue
        grade = (existing or {}).get(criterion)
        if grade is None and criterion == "policy_adherence":
            grade = _default_policy_adherence_grade(risk_policy_state)
        target[key] = "Pass" if grade is None else grade.capitalize()


def _initialize_assessment_draft(
    defaults: dict[str, object],
    state: MutableMapping[str, object] | None = None,
) -> None:
    """Set draft defaults once so validation reruns preserve local edits."""

    target = st.session_state if state is None else state
    for key, value in defaults.items():
        target.setdefault(key, value)


def _render_post_trade_review_dialog(repo: SQLiteJournalRepository, account: AccountListItem, trade, score: TradeProcessScore, profiles) -> None:  # type: ignore[no-untyped-def]
    existing = repo.get_post_trade_assessment_for_trade(trade.id)
    # A prior "auto" row is not a human review — only a "manual" row is safe to pre-fill
    # from (correction). An auto row's own defaults are neutral placeholders, not judgments;
    # only its evidence-backed policy_adherence value is reused, further below.
    existing_manual = existing if existing is not None and existing.method == "manual" else None
    queue = tuple(st.session_state.get("post-trade-review-queue", ()))
    strategy = repo.get_account_strategy(account.id)
    policy = repo.get_active_risk_policy(account.id)
    rule_settings = repo.get_framework_rule_settings(account.id)
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
    active_setups = repo.list_strategy_setups(strategy.id)
    active_sessions = repo.list_review_context_tags("session")
    active_regimes = repo.list_review_context_tags("regime")
    setup_options = [None, *active_setups]
    session_options = [None, *active_sessions]
    regime_options = [None, *active_regimes]
    draft_defaults: dict[str, object] = {
        f"assessment-{trade.id}-setup": _review_context_option(
            active_setups, existing_manual.setup_snapshot if existing_manual else None
        ),
        f"assessment-{trade.id}-session": _review_context_option(
            active_sessions, existing_manual.session_snapshot if existing_manual else None
        ),
        f"assessment-{trade.id}-regime": _review_context_option(
            active_regimes, existing_manual.regime_snapshot if existing_manual else None
        ),
        f"assessment-{trade.id}-note": existing_manual.post_review_note if existing_manual else "",
        f"assessment-{trade.id}-action": (
            existing_manual.corrective_action if existing_manual and existing_manual.corrective_action else ""
        ),
        f"assessment-{trade.id}-violations": (
            [code for code in existing_manual.violation_codes if code in REVIEW_MISTAKE_CODES]
            if existing_manual
            else []
        ),
        f"assessment-{trade.id}-hard-rules": list(existing_manual.hard_rule_codes) if existing_manual else [],
        f"assessment-{trade.id}-actual-risk": (
            existing_manual.declared_actual_risk_amount
            if existing_manual and existing_manual.declared_actual_risk_amount
            else ""
        ),
    }
    _initialize_assessment_draft(draft_defaults)
    _initialize_assessment_grades(
        trade.id,
        existing_manual.criterion_grades if existing_manual else None,
        score.risk_policy_state,
    )
    pillars = (
        ("Psychology", PSYCHOLOGY_CRITERIA),
        ("Risk management", RISK_CRITERIA),
        ("Trading system", SYSTEM_CRITERIA),
    )
    with st.form(
        f"assessment-{trade.id}-form",
        clear_on_submit=False,
        enter_to_submit=False,
        border=False,
    ):
        disband_requested = _render_imported_execution(repo, account, trade, score)
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
        st.markdown(tr("##### Assessment"))
        st.caption(f"Trading system: **{strategy.name}** (bound to this account)")
        st.caption("\\* Required")
        st.caption(tr("Change any Partial or Fail exceptions, then save once."))
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
        grades: dict[str, str | None] = {}
        pillar_columns = st.columns(3, gap="small", border=True)
        for pillar_column, (title, criteria) in zip(pillar_columns, pillars, strict=True):
            pillar_column.markdown(f"###### :{PILLAR_ACCENT_COLORS[title]}[{tr(title)}]")
            for criterion in criteria:
                with pillar_column:
                    grades[criterion] = _grade_control(
                        f"{tr(CRITERION_LABELS[criterion])} *",
                        key=f"assessment-{trade.id}-{criterion}",
                        help_text=CRITERION_HELP.get(criterion),
                    )
        reflection_column, evidence_column = st.columns([1.5, 1], gap="small")
        with reflection_column.container(border=True):
            st.markdown(f"##### {tr('Reflection and action')}")
            note = st.text_area(
                f"{tr('What happened and what did you learn?')} *",
                key=f"assessment-{trade.id}-note",
                placeholder=tr("Describe execution independently of P&L."),
                height=160,
            )
            action = st.text_area(
                tr("Corrective action"),
                key=f"assessment-{trade.id}-action",
                placeholder=tr("Required when any criterion is Partial or Fail, or a hard rule is selected."),
                height=160,
            )
        with evidence_column.container(border=True):
            st.markdown(f"##### {tr('Mistakes and rule breaches')}")
            violation_codes = st.multiselect(
                tr("Trading mistakes"),
                options=REVIEW_MISTAKE_CODES,
                key=f"assessment-{trade.id}-violations",
                format_func=lambda code: f"{MISTAKE_CATEGORY_PREFIXES[code]} · {tr(VIOLATION_LABELS[code])}",
                placeholder=tr("Select any mistakes made"),
                help=tr("Choose all mistakes that affected this trade. Leave empty if the trade followed your plan."),
            )
            hard_rules = st.multiselect(
                tr("Hard-rule events"),
                options=available_hard_rules,
                key=f"assessment-{trade.id}-hard-rules",
                format_func=lambda code: tr(HARD_RULE_LABELS[code]),
                help=tr("Enabled events selected on save set Hard-rule status to Fail. That result is snapshotted for this assessment, so later Review rules changes do not rewrite it. Automatic Risk limits are monitoring evidence, not hard failures by themselves."),
            )
            if not available_hard_rules:
                st.caption(tr("No hard-rule events are enabled. Enable one in Settings → Review rules to record it on a new assessment."))
        with evidence_column.container(border=True):
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
                key=f"assessment-{trade.id}-actual-risk",
                placeholder=tr("Enter a verified amount when automatic evidence is not sufficient"),
                help=tr("Overrides automatic evidence for this logical trade's policy comparison. It does not rewrite imported MT5 member positions or logical-trade account-limit monitoring."),
            )
        with st.container(horizontal=True, horizontal_alignment="left"):
            submitted = st.form_submit_button(
                "Update assessment" if existing_manual else "Save assessment",
                type="primary",
                icon=":material/save:",
            )
            submit_and_next = (
                st.form_submit_button(
                    tr("Save & review next ({count} left)", count=len(queue)),
                    icon=":material/skip_next:",
                )
                if queue
                else False
            )
    if disband_requested:
        _snapshot_review_before_disband(account.id, trade.id, queue)
        _begin_logical_trade_disband(repo, account, trade.id)
        return
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
        _clear_assessment_draft(trade.id)
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
    superseded = repo.list_superseded_post_trade_assessments_for_trade(
        account_id=account_id,
        logical_trade_id=trade.id,
    )
    if not superseded:
        return
    with st.expander(f"Assessment history ({len(superseded)})"):
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


def _pnl_cell_style(value: object) -> str:
    formatted = str(value)
    if formatted.startswith("+"):
        return "color: #0e9163; font-weight: 600"
    if formatted.startswith(("−", "-")):
        return "color: #c73545; font-weight: 600"
    return ""


def _render_logical_trade_merge_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    selected_logical_trade_ids: tuple[int, ...] = (),
) -> None:  # type: ignore[no-untyped-def]
    """Combine selected logical trades; imported MT5 positions stay immutable."""
    units = repo.list_closed_trades_for_review(account.id)
    unit_by_id = {unit.id: unit for unit in units}
    selected_logical_trade_ids = tuple(
        logical_trade_id for logical_trade_id in selected_logical_trade_ids if logical_trade_id in unit_by_id
    )
    if len(selected_logical_trade_ids) < 2:
        st.warning(tr("At least two selected logical trades are required. Return to the register and select the trades again."))
        return
    selected_position_trade_ids = tuple(
        member.id
        for logical_trade_id in selected_logical_trade_ids
        for member in unit_by_id[logical_trade_id].members
    )
    st.caption("The selected logical trades will be combined into a new logical trade. Each selected trade moves with all of its positions.")
    selected_units = [unit_by_id[logical_trade_id] for logical_trade_id in selected_logical_trade_ids]
    pnl_column = tr("P&L")
    selected_units_frame = pd.DataFrame(
        [
            {
                tr("Logical trade"): f"LT-{unit.id}",
                tr("Trade"): unit.display_label,
                tr("Positions"): unit.position_count,
                pnl_column: format_currency(unit.net_pnl, account.account_currency),
                tr("Position IDs"): ", ".join(f"#{position_id}" for position_id in unit.position_ids),
            }
            for unit in selected_units
        ]
    )
    st.dataframe(
        selected_units_frame.style.map(_pnl_cell_style, subset=[pnl_column]),
        hide_index=True,
        width="stretch",
    )
    combined_pnl = sum((Decimal(unit.net_pnl) for unit in selected_units), Decimal("0"))
    combined_pnl_text = format_currency(combined_pnl, account.account_currency)
    combined_pnl_color = "green" if combined_pnl > 0 else "red" if combined_pnl < 0 else "gray"
    st.markdown(f"### {tr('Combined P&L')} · :{combined_pnl_color}[{combined_pnl_text}]")
    st.caption("A new logical-trade ID will be created. Source labels are not carried forward automatically.")
    with st.form(f"logical-trade-group-{account.id}"):
        label = st.text_input(
            "Trade label (optional)",
            placeholder="e.g. London breakout scale-in",
        )
        save = st.form_submit_button(
            "Create new logical trade",
            type="primary",
            icon=":material/group_work:",
        )
    if not save:
        return
    try:
        preview = repo.preview_logical_trade_regroup(
            account_id=account.id,
            position_trade_ids=selected_position_trade_ids,
            logical_trade_id=None,
            source_logical_trade_ids=selected_logical_trade_ids,
        )
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["logical-trade-regroup-confirmation"] = {
        "account_id": account.id,
        "logical_trade_id": None,
        "position_trade_ids": selected_position_trade_ids,
        "source_logical_trade_ids": selected_logical_trade_ids,
        "display_label": label,
        "mode": "merge",
        "affected_assessment_count": preview.affected_assessment_count,
        "affected_assessment_labels": preview.affected_assessment_labels,
    }
    st.rerun()


def _render_logical_trade_regroup_confirmation(repo: SQLiteJournalRepository, account: AccountListItem, confirmation: dict) -> None:  # type: ignore[type-arg]
    count = confirmation["affected_assessment_count"]
    is_disband = confirmation["mode"] == "disband"
    if is_disband:
        group = next(
            (
                trade
                for trade in repo.list_closed_trades_for_review(account.id)
                if trade.id == confirmation["logical_trade_id"]
            ),
            None,
        )
        if group is None:
            st.error(tr("Logical trade no longer exists."))
            return
        st.markdown(
            f"**{tr('{count} positions will become standalone logical trades.', count=group.position_count)}**"
        )
        pnl_column = tr("P&L")
        position_frame = pd.DataFrame(
            [
                {
                    tr("Position"): f"#{member.position_id or '—'}",
                    tr("Opened"): _reporting_time(repo, member.entry_time, member.server_utc_offset_minutes),
                    tr("Closed"): _reporting_time(repo, member.exit_time, member.server_utc_offset_minutes),
                    pnl_column: format_currency(member.net_pnl, account.account_currency),
                }
                for member in group.members
            ]
        )
        st.dataframe(
            position_frame.style.map(_pnl_cell_style, subset=[pnl_column]),
            hide_index=True,
            width="stretch",
        )
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
        st.caption(
            tr("No saved assessment will be affected.")
            if is_disband
            else "No active saved assessment is affected. Dashboard reporting will recalculate from the new grouping."
        )
    with st.container(horizontal=True, gap="small"):
        confirm = st.button(
            {
                "disband": "Confirm disband",
                "merge": "Confirm & review",
            }.get(confirmation["mode"], "Confirm & review"),
            type="primary",
            icon=":material/check:",
        )
        secondary = st.button(
            tr("Cancel") if is_disband else "Back",
            icon=":material/close:" if is_disband else ":material/arrow_back:",
        )
    if secondary:
        if is_disband:
            _restore_review_after_disband_cancel()
        else:
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
    if confirmation["mode"] == "disband":
        _finish_review_after_disband()
    elif result.logical_trade_id is not None:
        st.session_state["post-trade-review-trade-id"] = result.logical_trade_id
        st.session_state["post-trade-review-queue"] = ()
    queue_toast(tr(notice))
    _defer_logical_trade_selection_reset(account.id)
    _clear_group_dialog()
    st.rerun()


def _render_review_register(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore], profiles) -> None:  # type: ignore[no-untyped-def]
    st.html(
        """
        <style>
        /* Each trade card's execution-detail row (Opened/Entry/Closed/Exit/Duration/
           Size) is scanned far more than it's studied - keep every field visible
           (unlike the rest of the card, which was consolidated into fewer, denser
           rows) but shrink it so it reads as a footnote rather than a second card. */
        div[class*="st-key-review-detail-"] {
            margin-top: 0.35rem;
            padding-top: 0.35rem;
            border-top: 1px solid var(--st-border-color, #c8d0c8);
        }
        div[class*="st-key-review-detail-"] [data-testid="stCaptionContainer"] p {
            font-size: 0.68rem;
            margin-bottom: 0;
        }
        div[class*="st-key-review-detail-"] [data-testid="stMarkdownContainer"] p {
            font-size: 0.8rem;
            margin-top: -0.15rem;
        }
        @media (max-width: 640px) {
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
    with st.container(horizontal=True, gap="small", vertical_alignment="top"):
        with st.container(border=True, width="content"):
            st.caption(tr("Status"))
            with st.container(horizontal=True, gap="small"):
                checked_by_key = {
                    key: st.checkbox(
                        filter_labels[key],
                        value=(key == "needs_approval"),
                        key=f"review-filter-{key}-{account.id}",
                    )
                    for key in filter_order
                }
        with st.container(border=True, width="content"):
            st.caption(tr("Direction"))
            with st.container(horizontal=True, gap="small"):
                direction_checked = {
                    direction: st.checkbox(
                        tr(direction.capitalize()),
                        value=True,
                        key=f"review-filter-direction-{direction}-{account.id}",
                    )
                    for direction in ("long", "short")
                }
    selected_keys = tuple(key for key in filter_order if checked_by_key[key])
    selected_directions = tuple(direction for direction, checked in direction_checked.items() if checked)
    filter_key = f"logical-trade-selection-filter-{account.id}"
    current_filter = (selected_keys, selected_directions)
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
        and trade.direction.casefold() in selected_directions
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
        elif not selected_directions:
            st.info(tr("Select at least one direction filter above to see trades."))
        else:
            status_text = " / ".join(tr(filter_names[key]).casefold() for key in selected_keys)
            st.info(tr("No {status} trades for this account.", status=status_text))
    else:
        position_by_id = {trade.id: index for index, (trade, _) in enumerate(visible)}
        start = (current_page - 1) * REVIEW_PAGE_SIZE
        page_items = visible[start : start + REVIEW_PAGE_SIZE]
        column_widths = [0.45, 1.7, 0.7, 0.8, 0.9, 1.5, 0.6, 1.1]
        column_labels = ("Select", "Trade", "Positions", "P&L", "Method", "Score", "Rules", "Actions")
        with st.container(key="trade-review-table-header"):
            header = st.columns(column_widths)
            for column, label in zip(header, column_labels, strict=True):
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
                select_column, trade_column, positions_column, pnl_column, method_column, score_column, process_column, actions_column = st.columns(
                    column_widths
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
                direction = direction_tag(trade.direction)
                outcome = outcome_tag(trade.net_pnl)
                position_count = len(trade.position_ids)
                position_label = tr("1 pos") if position_count == 1 else tr("{count} pos", count=position_count)
                with trade_column:
                    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                        st.markdown(f"**LT-{trade.id}**")
                        st.write(trade.symbol)
                        st.badge(tr(direction.label), color=direction.color, icon=direction.icon)
                        if trade.custom_label:
                            st.write(trade.custom_label)
                positions_column.badge(
                    position_label,
                    color="blue" if position_count > 1 else "gray",
                    icon=":material/layers:" if position_count > 1 else None,
                    help=", ".join(f"#{position_id}" for position_id in trade.position_ids),
                )
                pnl_column.badge(format_currency(trade.net_pnl, account.account_currency), color=outcome.color)
                method_column.badge(tr(review), color="gray")
                with score_column:
                    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                        st.badge(
                            _score_text(score.overall_score),
                            color=_SCORE_BADGE_COLOR.get(score.quality_status, "gray"),
                        )
                        st.caption(_prs_summary(score))
                if score.process_status == "FAIL":
                    process_column.badge(tr("Fail"), icon=":material/error:", color="red")
                elif score.process_status == "PASS":
                    process_column.badge(tr("Clear"), icon=":material/check:", color="green")
                else:
                    process_column.write("—")
                with actions_column:
                    with st.container(horizontal=True, gap="small"):
                        if st.button(
                            "Review",
                            key=f"open-logical-trade-review-{account.id}-{trade.id}",
                            type="primary",
                            icon=":material/edit:",
                        ):
                            st.session_state["post-trade-review-trade-id"] = trade.id
                            st.session_state["post-trade-review-queue"] = tuple(
                                item.id for item, _ in visible[position_by_id[trade.id] + 1 :]
                            )
                            st.rerun()
                        has_quick_action = score.review_kind in {"needs_approval", "auto_review"}
                        if has_quick_action or trade.is_group:
                            with st.popover("", icon=":material/more_vert:", help=tr("More actions")):
                                if has_quick_action:
                                    is_within_policy = score.review_kind == "auto_review"
                                    label = "Approve" if is_within_policy else "Quick review"
                                    help_text = (
                                        "Approve this within-policy automatic risk evidence so it counts toward your pillar scores."
                                        if is_within_policy
                                        else "Accept the automatic risk evidence in one click instead of a full Zone-aligned 12-criterion review."
                                    )
                                    if st.button(
                                        label,
                                        key=f"approve-auto-review-{account.id}-{trade.id}",
                                        icon=":material/check:",
                                        help=help_text,
                                        width="stretch",
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
                                if trade.is_group and st.button(
                                    "Ungroup",
                                    key=f"ungroup-logical-trade-{account.id}-{trade.id}",
                                    icon=":material/group_off:",
                                    width="stretch",
                                ):
                                    _begin_logical_trade_disband(repo, account, trade.id)
                opened_at = _reporting_time(repo, trade.entry_time, trade.server_utc_offset_minutes)
                closed_at = _reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes)
                with st.container(key=f"review-detail-{trade.id}"):
                    execution_columns = st.columns([1.25, 1, 1.25, 1, 0.8, 0.8])
                    execution_values = (
                        ("Opened", opened_at),
                        ("Entry price", _format_execution_number(trade.entry_price, reference=trade.members[0].entry_price)),
                        ("Closed", closed_at),
                        ("Exit price", _format_execution_number(trade.exit_price, reference=trade.members[0].exit_price)),
                        ("Duration", _format_trade_duration(trade.entry_time, trade.exit_time)),
                        ("Size", f"{_format_execution_number(trade.volume)} {tr('lots')}"),
                    )
                    for detail_column, (label, value) in zip(execution_columns, execution_values, strict=True):
                        detail_column.caption(tr(label))
                        detail_column.markdown(f"**{value}**")
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.badge(
                        tr(score.classification or "Unclassified"),
                        color=_SCORE_BADGE_COLOR.get(score.quality_status, "gray"),
                    )
                    detail_parts = [_auto_risk_label(score)]
                    if score.rubric_version is not None:
                        detail_parts.append(_rubric_label(score.rubric_version))
                    st.caption(" · ".join(detail_parts))
                if failure_detail := _process_failure_detail(score):
                    st.caption(failure_detail)
                if monitoring_detail := _automatic_risk_monitoring_detail(score):
                    st.caption(monitoring_detail)
        st.caption("Automatic risk evidence only counts toward scores once approved here in one click, or replaced by a full assessment.")
    group_dialog_active = False
    group_editor = st.session_state.get("logical-trade-group-editor")
    if group_editor is not None and group_editor.get("account_id") == account.id:
        confirmation = st.session_state.get("logical-trade-regroup-confirmation")
        if confirmation is not None and confirmation.get("account_id") == account.id:
            group_dialog_active = True
            title = "Disband logical trade" if confirmation.get("mode") == "disband" else "Group logical trades"
            st.dialog(tr(title), width="large", on_dismiss=_dismiss_group_dialog)(_render_logical_trade_regroup_confirmation)(
                repo, account, confirmation
            )
        elif group_editor.get("logical_trade_id") is None:
            group_dialog_active = True
            st.dialog(tr("Group logical trades"), width="large", on_dismiss=_dismiss_group_dialog)(_render_logical_trade_merge_dialog)(
                repo,
                account,
                tuple(group_editor.get("selected_logical_trade_ids", ())),
            )
        else:
            _clear_group_dialog()
    if st.session_state.get(_bulk_quick_review_key(account.id)) is not None:
        st.dialog(tr("Quick review selected trades"), width="large", on_dismiss=_dismiss_bulk_quick_review)(_render_bulk_quick_review_dialog)(repo, account, trades, scores)
    if not group_dialog_active:
        _render_selected_post_trade_review_dialog(repo, account, ordered, scores, profiles)


def _render_selected_post_trade_review_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    ordered,
    scores: dict[int, TradeProcessScore],
    profiles,
) -> None:  # type: ignore[no-untyped-def]
    """Render the shared assessment dialog for the current session selection."""

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


def render_post_trade_review_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    service: FrameworkService,
) -> None:
    """Render the selected assessment dialog using its workspace reporting calendar."""

    if st.session_state.get("post-trade-review-trade-id") is None:
        return
    ordered = sorted(
        repo.list_closed_trades_for_review(account.id),
        key=lambda item: (item.exit_time, item.id),
        reverse=True,
    )
    scores = {
        item.trade_id: item
        for item in service.trade_process_scores(account.id)
    }
    profiles = [repo.get_account_strategy(account.id)]
    _render_selected_post_trade_review_dialog(repo, account, ordered, scores, profiles)


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
    critical_threshold = repo.get_framework_rule_settings(account.id).repeated_critical_threshold
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
            _render_help_popover("Zone-aligned series follow the selected rolling window.")
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


def render_compact_framework_focus(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    service: FrameworkService,
) -> None:
    """Render the shared coaching action card without page-specific history."""

    _render_framework_focus(repo, account, service, (), compact=True, show_heading=False)


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
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.markdown(f"**{tr(f'Ongoing {status.cadence}')}**")
                    st.badge(f"{status.period_start} → {status.period_end}", color="gray")
                if status.closed_trades:
                    st.progress(
                        status.reviewed_trades / status.closed_trades,
                        text=tr(
                            "{reviewed} of {closed} reviewed · {pending} pending",
                            reviewed=format_count(status.reviewed_trades),
                            closed=format_count(status.closed_trades),
                            pending=format_count(pending),
                        ),
                    )
                else:
                    st.caption(tr("No closed trades yet"))
                st.caption(tr("Review opens {date}.", date=status.review_opens_on))

    st.markdown(f"###### {tr('Latest completed periods')}")
    statuses = [service.period_review_status(account.id, cadence) for cadence in ("weekly", "monthly")]
    with st.container(horizontal=True, gap="small"):
        for status in statuses:
            if status.disposition == "skipped":
                status_label, status_color = tr("Skipped"), "gray"
            elif status.disposition == "reviewed":
                status_label, status_color = tr("Reviewed"), "green"
            elif status.closed_trades == 0:
                status_label, status_color = tr("No activity"), "gray"
            elif status.reviewed_trades == 0:
                status_label, status_color = tr("Pending review"), "orange"
            elif status.due:
                status_label, status_color = tr("Due"), "orange"
            else:
                status_label, status_color = tr("Up to date"), "green"
            with st.container(border=True):
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.markdown(f"**{tr(f'Last completed {status.cadence}')}**")
                    st.badge(status_label, color=status_color)
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
        for status in backlog:
            pending = status.closed_trades - status.reviewed_trades
            with st.container(border=True):
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.markdown(f"**{tr(f'{status.cadence.capitalize()} review')}**")
                    st.caption(f"{status.period_start} to {status.period_end}")
                    st.badge(
                        tr("Review due") if status.due else tr("Review trades first"),
                        color="orange" if status.due else "blue",
                    )
                    st.caption(
                        tr(
                            "{reviewed}/{closed} reviewed · {pending} pending",
                            reviewed=format_count(status.reviewed_trades),
                            closed=format_count(status.closed_trades),
                            pending=format_count(pending),
                        )
                    )

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
        submitted = st.form_submit_button("Save risk policy", type="primary")
    if submitted:
        policy_values = {
            "account_id": account.id,
            "standard_risk_per_trade_percent": str(standard),
            "maximum_risk_per_trade_percent": str(maximum),
            "daily_loss_limit_r": str(daily),
            "weekly_loss_limit_r": str(weekly),
            "max_drawdown_percent": str(drawdown),
            "max_open_risk_r": str(open_risk),
            "max_consecutive_losses": int(streak),
            "minimum_rr": str(minimum_rr),
            "correlation_policy": correlation,
            "drawdown_reset_period": reset_period_options[drawdown_reset_label],
            "loss_streak_reset_period": reset_period_options[streak_reset_label],
        }

        def save_policy(**confirmation):  # type: ignore[no-untyped-def]
            try:
                with st.spinner(tr("Saving…")):
                    repo.save_account_risk_policy(**policy_values, **confirmation)
            except ValueError as error:
                st.error(str(error))
            else:
                queue_toast(tr("Risk policy saved as a new version."))
                st.rerun()

        change_required = True
        if policy is not None:
            try:
                change_required = repo.risk_policy_change_required(**policy_values)
            except ValueError as error:
                st.error(str(error))
                return

        if policy is None:
            save_policy()
        elif not change_required:
            st.info(tr("No risk-policy changes to save."), icon=":material/info:")
        else:
            preview = repo.preview_risk_policy_change(account.id)

            @st.dialog(tr("Confirm account-wide recalculation"), width="large")
            def confirm_policy_change() -> None:
                st.warning(
                    "This replaces the active analytical policy for this account and recalculates all derived historical Risk and R metrics.",
                    icon=":material/calculate:",
                )
                st.markdown(
                    f"**Affected:** {preview.affected_logical_trades} logical trades  \n"
                    f"**Preserved unchanged:** {preview.preserved_assessments} saved assessments and "
                    f"{preview.preserved_period_reviews} saved weekly/monthly reviews  \n"
                    "MT5 trades and funded capital are not modified. The prior policy version remains in audit history."
                )
                confirmed = st.checkbox(
                    tr("I understand current analytics for this account will be recalculated"),
                    key=f"confirm-risk-recalculation-{account.id}-{preview.expected_active_policy_id}",
                )
                if st.button(tr("Confirm and create new policy version"), type="primary", disabled=not confirmed):
                    save_policy(
                        expected_active_policy_id=preview.expected_active_policy_id,
                        confirm_recalculation=True,
                    )

            confirm_policy_change()


def _render_framework_rules(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    settings = repo.get_framework_rule_settings(account.id)
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
                    account_id=account.id,
                    oversized_revenge_hard=revenge, mandatory_setup_hard=setup, stop_widened_hard=stop,
                    shutdown_breach_hard=shutdown, repeated_critical_threshold=int(threshold),
                )
        except ValueError as error:
            st.error(str(error))
        else:
            queue_toast(tr("Review rules saved."))
            st.rerun()
