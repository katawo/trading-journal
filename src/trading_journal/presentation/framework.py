"""Native Streamlit presentation for the greenfield three-pillar framework."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pandas as pd
import streamlit as st

from trading_journal.application.framework import (
    PILLAR_NAMES,
    ROADMAP_ITEMS,
    FrameworkService,
    PillarScore,
    RiskSnapshot,
    TradeProcessScore,
)
from trading_journal.application.reporting_time import reporting_datetime
from trading_journal.infrastructure.sqlite_repository import (
    PSYCHOLOGY_CRITERIA,
    RISK_CRITERIA,
    SYSTEM_CRITERIA,
    AccountListItem,
    SQLiteJournalRepository,
)


GRADE_OPTIONS = ("Pass", "Partial", "Fail")
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


def _account_label(account: AccountListItem) -> str:
    return f"{account.display_name} · {account.login} · {account.broker_server}"


def _select_account(repo: SQLiteJournalRepository, *, key: str) -> AccountListItem | None:
    accounts = repo.list_mt5_accounts()
    if not accounts:
        st.info("Add an approved MT5 account in Settings before using the framework.")
        return None
    return st.selectbox("Trading account", accounts, format_func=_account_label, key=key)


def _score_text(value: str | None) -> str:
    return "—" if value is None else f"{Decimal(value):.0f}%"


def _state_label(snapshot: RiskSnapshot) -> str:
    return {"clear": "Clear", "caution": "Caution", "stop": "Stop", "unconfigured": "Set up"}[snapshot.state]


def _auto_risk_label(score: TradeProcessScore) -> str:
    evidence = score.auto_risk
    state = {"within_policy": "Within policy", "over_policy": "Over policy", "unavailable": "Unavailable"}[evidence.state]
    source = {
        "specific_preset_sl": "Preset SL",
        "real_loss_sl": "Real-loss estimate",
        "live_account_balance_sl": "Live-balance estimate",
        "unavailable": "No source",
    }[evidence.risk_basis]
    return f"{source} · {state}"


def _reporting_time(repo: SQLiteJournalRepository, value: str, server_utc_offset_minutes: int) -> str:
    """Show execution time in the same calendar used for reports and alerts."""
    basis = repo.get_journal_settings().reporting_time_basis
    return reporting_datetime(value, server_utc_offset_minutes, basis).strftime("%Y-%m-%d %H:%M:%S")


def _render_score_cards(scores: tuple[PillarScore, ...]) -> None:
    with st.container(horizontal=True, gap="small"):
        for score in scores:
            label = "FAIL" if score.hard_block else "Incomplete" if score.score is None else score.status.capitalize()
            st.metric(PILLAR_NAMES[score.pillar], _score_text(score.score), f"{label} · {score.sample_size} in sample", border=True)


def _render_alerts(service: FrameworkService, account_id: int) -> None:
    alerts = service.framework_alerts(account_id)
    if not alerts:
        st.success("No active framework alerts.", icon=":material/check_circle:")
        return
    for alert in alerts:
        if alert.severity == "critical":
            st.error(alert.message, icon=":material/error:")
        elif alert.severity == "warning":
            st.warning(alert.message, icon=":material/warning:")
        else:
            st.info(alert.message, icon=":material/info:")


def render_framework_dashboard(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    """Compact monitoring inside the main performance dashboard."""
    service = FrameworkService(repo)
    snapshot = service.risk_snapshot(account.id)
    scores = service.pillar_scores(account.id)
    readiness = service.readiness(account.id)
    st.markdown("#### Three-pillar monitor")
    st.caption(f"Psychology and System are trader-wide. Risk is scoped to {_account_label(account)}.")
    _render_alerts(service, account.id)
    with st.container(horizontal=True, gap="small"):
        st.metric("Readiness", _score_text(readiness.score), readiness.status.capitalize(), border=True)
        st.metric("Risk state", _state_label(snapshot), border=True)
        st.metric("Today", "—" if snapshot.daily_r is None else f"{Decimal(snapshot.daily_r):+.2f}R", border=True)
        st.metric("Max drawdown", "—" if snapshot.max_drawdown_percent is None else f"{Decimal(snapshot.max_drawdown_percent):.2f}%", border=True)
    _render_score_cards(scores)
    st.caption(readiness.detail)


def render_framework_page(repo: SQLiteJournalRepository) -> None:
    st.markdown('<div class="dashboard-kicker">POST-TRADE JOURNAL</div>', unsafe_allow_html=True)
    st.subheader("Three-pillar framework")
    st.caption("Use completed MT5 trades to assess execution. Alerts are advisory; this journal never sends, blocks, or changes MT5 orders.")
    account = _select_account(repo, key="framework-account")
    if account is None:
        return
    review_tab, monitor_tab, roadmap_tab, policy_tab, rules_tab = st.tabs(["Review trades", "Monitor", "Roadmap", "Risk policy", "Framework rules"])
    with review_tab:
        _render_post_trade_review(repo, account)
    with monitor_tab:
        _render_monitor(repo, account)
    with roadmap_tab:
        _render_roadmap(repo, account)
    with policy_tab:
        _render_risk_policy(repo, account)
    with rules_tab:
        _render_framework_rules(repo)


def _render_post_trade_review(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    snapshot = service.risk_snapshot(account.id)
    st.markdown("#### Closed-trade reviews")
    st.caption(f"Risk state: {_state_label(snapshot)} · {snapshot.message}")
    trades = repo.list_closed_trades_for_review(account.id)
    if not trades:
        st.info("No completed MT5 positions have been imported for this account yet.")
        return
    profiles = repo.list_strategy_profiles()
    if not profiles:
        st.warning("Create a strategy in Settings before saving a full three-pillar review.", icon=":material/info:")
    trade_scores = {item.trade_id: item for item in service.trade_process_scores(account.id)}
    _render_review_register(repo, account, trades, trade_scores, profiles)


def _clear_review_dialog() -> None:
    st.session_state.pop("post-trade-review-trade-id", None)


def _render_imported_execution(repo: SQLiteJournalRepository, account: AccountListItem, trade, score: TradeProcessScore) -> None:  # type: ignore[no-untyped-def]
    with st.container(horizontal=True, gap="small"):
        st.metric("Symbol", trade.symbol, border=True)
        st.metric("Position", f"#{trade.position_id or '—'}", border=True)
        st.metric(f"P&L ({account.account_currency})", f"{Decimal(trade.net_pnl):+.2f}", border=True)
        st.metric("Automatic risk", _auto_risk_label(score), border=True)
    st.caption(f"Entry {_reporting_time(repo, trade.entry_time, trade.server_utc_offset_minutes)} · Exit {_reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes)}. MT5 execution data is read-only.")
    st.caption(score.auto_risk.detail)


def _grade_control(label: str, *, existing: str | None, key: str) -> str | None:
    default = existing.capitalize() if existing else None
    choice = st.segmented_control(label, GRADE_OPTIONS, default=default, key=key, width="stretch")
    return None if choice is None else choice.casefold()


@st.dialog("Post-trade assessment", width="large", on_dismiss=_clear_review_dialog)
def _render_post_trade_review_dialog(repo: SQLiteJournalRepository, account: AccountListItem, trade, score: TradeProcessScore, profiles) -> None:  # type: ignore[no-untyped-def]
    existing = repo.get_post_trade_assessment_for_trade(trade.id)
    st.caption(f"{'Correct' if existing else 'Review'} #{trade.position_id or '—'} · {trade.symbol}")
    _render_imported_execution(repo, account, trade, score)
    if not profiles:
        st.warning("A Strategy profile is required for a full post-trade assessment.")
        return
    policy = repo.get_active_risk_policy(account.id)
    default_id = existing.strategy_profile_id if existing else score.mapped_strategy.id if score.mapped_strategy else _default_strategy_id(repo)
    strategy_index = next((index for index, item in enumerate(profiles) if item.id == default_id), 0)
    st.markdown("##### Assessment")
    with st.form(f"post-trade-assessment-{trade.id}"):
        strategy = st.selectbox("Strategy", profiles, index=strategy_index, format_func=lambda item: item.name)
        grades: dict[str, str | None] = {}
        for title, criteria in (("Psychology", PSYCHOLOGY_CRITERIA), ("Risk management", RISK_CRITERIA), ("Trading system", SYSTEM_CRITERIA)):
            with st.container(border=True):
                st.markdown(f"**{title}**")
                first, second = st.columns(2)
                for index, criterion in enumerate(criteria):
                    target = first if index % 2 == 0 else second
                    with target:
                        grades[criterion] = _grade_control(
                            CRITERION_LABELS[criterion],
                            existing=existing.criterion_grades.get(criterion) if existing else None,
                            key=f"assessment-{trade.id}-{criterion}",
                        )
        actual_risk = st.text_input(
            "Actual risk amount (optional)",
            value=existing.declared_actual_risk_amount if existing and existing.declared_actual_risk_amount else "",
            placeholder="Enter a verified amount when automatic evidence is not sufficient",
        )
        if policy is not None:
            st.caption(f"Risk policy v{policy.version}: Standard 1R {policy.standard_risk_per_trade_percent}% · maximum {policy.maximum_risk_per_trade_percent}%.")
        else:
            st.caption("No active Risk policy is attached; the assessment still records your judgement, while automatic limit checks remain unavailable.")
        violation_codes = st.multiselect(
            "Reason tags",
            options=list(VIOLATION_LABELS),
            default=list(existing.violation_codes) if existing else [],
            format_func=VIOLATION_LABELS.get,
            help="Tag the cause of a partial or failed assessment so period reviews can identify recurring issues.",
        )
        hard_rules = st.multiselect(
            "Hard-rule events",
            options=list(HARD_RULE_LABELS),
            default=list(existing.hard_rule_codes) if existing else [],
            format_func=HARD_RULE_LABELS.get,
            help="A hard-rule event marks Process Quality as FAIL. It remains an advisory journal alert, not an MT5 trade lock.",
        )
        note = st.text_area("What happened and what did you learn?", value=existing.post_review_note if existing else "", placeholder="Describe execution independently of P&L.")
        action = st.text_area("Corrective action", value=existing.corrective_action if existing and existing.corrective_action else "", placeholder="Required for partial, failed, or hard-rule reviews.")
        submitted = st.form_submit_button("Update assessment" if existing else "Save assessment", type="primary")
    if not submitted:
        _render_review_history(repo, trade.id)
        return
    if any(value is None for value in grades.values()):
        st.error("Rate every criterion as Pass, Partial, or Fail before saving.")
        return
    try:
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
        )
    except ValueError as error:
        st.error(str(error))
    else:
        st.session_state["post-trade-review-notice"] = "Post-trade assessment saved."
        _clear_review_dialog()
        st.rerun()


def _render_review_history(repo: SQLiteJournalRepository, trade_id: int) -> None:
    revisions = repo.list_post_trade_assessment_revisions(trade_id)
    if not revisions:
        return
    with st.expander(f"Assessment history ({len(revisions)})"):
        for revision in revisions:
            failed = sum(value == "fail" for value in revision.criterion_grades.values())
            st.markdown(f"**Version {revision.version}** · {revision.archived_at[:19]} · {revision.strategy_snapshot.name}")
            st.caption(f"{failed} failed criterion/criteria · Hard rules: {', '.join(revision.hard_rule_codes) or 'none'}")
            st.write(revision.post_review_note)


def _default_strategy_id(repo: SQLiteJournalRepository) -> int | None:
    try:
        return repo.get_journal_settings().default_strategy_profile_id
    except RuntimeError:
        return None


def _render_review_register(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore], profiles) -> None:  # type: ignore[no-untyped-def]
    ordered = sorted(trades, key=lambda item: (item.exit_time, item.id), reverse=True)
    groups = {
        "Needs review": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].assessment_state == "not_scored"],
        "Automatic risk evidence": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].assessment_state == "automatic_evidence"],
        "Reviewed": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].assessment_state == "reviewed"],
        "Failed": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].process_status == "FAIL"],
    }
    with st.container(horizontal=True, gap="small"):
        for label in ("Needs review", "Automatic risk evidence", "Reviewed", "Failed"):
            st.metric(label, len(groups[label]), border=True)
    filter_value = st.segmented_control(
        "Review status", ["Needs review", "Automatic risk evidence", "Reviewed", "Failed", "All"],
        default="Needs review", required=True, width="content", key=f"review-filter-{account.id}",
    )
    visible = [(trade, scores[trade.id]) for trade in ordered] if filter_value == "All" else groups[filter_value]
    if not visible:
        st.info(f"No {filter_value.casefold()} trades for this account.")
        return
    rows = []
    for trade, score in visible:
        review = "Needs review" if score.assessment_state == "not_scored" else "Automatic evidence" if score.assessment_state == "automatic_evidence" else "Reviewed"
        rows.append({
            "Closed": _reporting_time(repo, trade.exit_time, trade.server_utc_offset_minutes),
            "Position": f"#{trade.position_id or '—'}",
            "Symbol": trade.symbol,
            "P&L": Decimal(trade.net_pnl),
            "Review": review,
            "Process": score.process_status or "—",
            "Classification": score.classification or "—",
            "Psychology": _score_text(score.psychology_score),
            "Risk": _score_text(score.risk_score),
            "System": _score_text(score.system_score),
            "Risk evidence": _auto_risk_label(score),
            "Open": ":material/edit:",
        })
    register_key = f"review-register-{account.id}-{filter_value.casefold().replace(' ', '-') }"

    def open_review() -> None:
        click = st.session_state.get(register_key)
        if click is not None and click["row"] < len(visible):
            st.session_state["post-trade-review-trade-id"] = visible[click["row"]][0].id

    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        column_config={
            "P&L": st.column_config.NumberColumn(format="%.2f"),
            "Open": st.column_config.ButtonColumn("", type="tertiary", help="Open assessment", on_click=open_review, key=register_key),
        },
    )
    st.caption("Automatic risk evidence is advisory. Only a saved assessment creates three-pillar scores, classifications, roadmap evidence, and readiness data.")
    selected = st.session_state.get("post-trade-review-trade-id")
    if selected is None:
        return
    item = next(((trade, score) for trade, score in [(trade, scores[trade.id]) for trade in ordered] if trade.id == selected), None)
    if item is None:
        _clear_review_dialog()
        return
    _render_post_trade_review_dialog(repo, account, item[0], item[1], profiles)


def _render_monitor(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    st.markdown("#### Monitoring and alerts")
    _render_alerts(service, account.id)
    window = st.segmented_control("Rolling sample", [20, 30, 50], default=20, required=True, width="content", key=f"framework-window-{account.id}")
    scores = service.pillar_scores(account.id, window=int(window))
    readiness = service.readiness(account.id, window=int(window))
    st.metric("Overall readiness", _score_text(readiness.score), readiness.status.capitalize(), border=True)
    st.caption(readiness.detail)
    _render_score_cards(scores)
    component_rows = [
        {"Pillar": PILLAR_NAMES[score.pillar], "Metric": name, "Score": _score_text(value), "Scope": score.scope}
        for score in scores for name, value in score.component_scores
    ]
    if component_rows:
        st.markdown("##### What drives the current scores")
        st.dataframe(pd.DataFrame(component_rows), hide_index=True, width="stretch")
    trend = service.rolling_score_trend(account.id, window=int(window))
    if trend:
        st.markdown("##### Selected-account execution trend")
        frame = pd.DataFrame(trend, columns=["Closed", "Psychology", "Risk", "System"]).set_index("Closed")
        st.line_chart(frame, width="stretch")
        st.caption("This trend uses selected-account reviewed trades. Score-card scopes remain explicit above.")
    classifications = Counter(score.classification for score in service.trade_process_scores(account.id) if score.classification is not None)
    issues = service.recurring_issues(account.id, window=int(window))
    left, right = st.columns(2)
    with left:
        st.markdown("##### Process-quality distribution")
        if classifications:
            st.bar_chart(pd.DataFrame({"Classification": list(classifications), "Trades": list(classifications.values())}).set_index("Classification"), width="stretch")
        else:
            st.caption("Save complete assessments to build a process-quality distribution.")
    with right:
        st.markdown("##### Recurring issues")
        if issues:
            st.dataframe(pd.DataFrame([{"Issue": VIOLATION_LABELS.get(issue, issue), "Count": count} for issue, count in issues]), hide_index=True, width="stretch")
        else:
            st.caption("No tagged recurring issues in this sample.")
    _render_period_reviews(repo, account, service)


def _render_period_reviews(repo: SQLiteJournalRepository, account: AccountListItem, service: FrameworkService) -> None:
    st.markdown("##### Weekly and monthly review")
    statuses = [service.period_review_status(account.id, cadence) for cadence in ("weekly", "monthly")]
    with st.container(horizontal=True, gap="small"):
        for status in statuses:
            st.metric(f"{status.cadence.capitalize()} review", "Due" if status.due else "Up to date", f"{status.period_start} to {status.period_end}", border=True)
    due = next((status for status in statuses if status.due), None)
    if due is not None:
        with st.form(f"period-review-{account.id}-{due.cadence}"):
            st.caption(f"Save the {due.cadence} reflection for {due.period_start} to {due.period_end}.")
            note = st.text_area("Review note", placeholder="What pattern did the data reveal?")
            action = st.text_area("One priority corrective action", placeholder="Choose one focused action for the next period.")
            submitted = st.form_submit_button("Save period review", type="primary")
        if submitted:
            try:
                service.save_period_review(account_id=account.id, cadence=due.cadence, review_note=note, priority_action=action)
            except ValueError as error:
                st.error(str(error))
            else:
                st.success("Period review saved.")
                st.rerun()
    reviews = repo.list_framework_period_reviews(account.id)
    if reviews:
        latest = reviews[0]
        with st.expander("Latest saved period review"):
            st.caption(f"{latest.cadence.capitalize()} · {latest.period_start} to {latest.period_end} · readiness {_score_text(latest.readiness_score)}")
            st.write(latest.review_note)
            st.markdown(f"**Priority action:** {latest.priority_action}")


def _render_roadmap(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    statuses = {item.pillar: item for item in service.roadmap_status(account.id)}
    evidence = {(item.pillar, item.level, item.item_key): item for item in repo.list_pillar_roadmap_evidence(account.id)}
    st.markdown("#### Parallel readiness roadmap")
    with st.container(horizontal=True, gap="small"):
        for pillar in PILLAR_NAMES:
            status = statuses[pillar]
            st.metric(PILLAR_NAMES[pillar], f"Level {status.current_level}", f"{status.completed_items}/{status.total_items} evidence", border=True)
    pillar = st.segmented_control("Roadmap pillar", list(PILLAR_NAMES), format_func=PILLAR_NAMES.get, default="psychology", required=True, width="content", key=f"roadmap-pillar-{account.id}")
    status = statuses[pillar]
    items = ROADMAP_ITEMS[pillar][status.current_level]
    st.markdown(f"##### {PILLAR_NAMES[pillar]} · Level {status.current_level}")
    st.caption(status.gate)
    if status.can_complete_current_level:
        with st.form(f"roadmap-{pillar}-{status.current_level}"):
            values: dict[str, tuple[bool, str]] = {}
            for item_key, label in items:
                existing = evidence.get((pillar, status.current_level, item_key))
                complete = st.checkbox(label, value=bool(existing and existing.completed), key=f"roadmap-complete-{pillar}-{status.current_level}-{item_key}")
                note = st.text_area("Evidence", value=existing.evidence_note if existing and existing.evidence_note else "", key=f"roadmap-note-{pillar}-{status.current_level}-{item_key}")
                values[item_key] = (complete, note)
            submitted = st.form_submit_button("Save roadmap evidence", type="primary")
        if submitted:
            try:
                for item_key, (complete, note) in values.items():
                    repo.save_pillar_roadmap_evidence(account_id=account.id, pillar=pillar, level=status.current_level, item_key=item_key, completed=complete, evidence_note=note)
            except ValueError as error:
                st.error(str(error))
            else:
                st.success("Roadmap evidence saved.")
                st.rerun()
    else:
        st.info(status.gate)


def _render_risk_policy(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    policy = repo.get_active_risk_policy(account.id)
    funded = repo.get_account_funded_capital(account.id)
    st.markdown("#### Account risk policy")
    st.caption("The policy defines reporting 1R and safety limits. It monitors closed MT5 trades only and never controls MT5.")
    if funded is None:
        st.warning("Set funded capital in Settings → MT5 Accounts before saving a Risk policy.")
    else:
        st.info(f"Funded capital: {funded} {account.account_currency}.")
    with st.form(f"account-risk-policy-{account.id}"):
        first, second, third = st.columns(3)
        standard = first.number_input("Standard risk (1R) (%)", min_value=0.01, value=float(policy.standard_risk_per_trade_percent) if policy else 0.5, step=0.05)
        maximum = second.number_input("Maximum risk per trade (%)", min_value=0.01, value=float(policy.maximum_risk_per_trade_percent) if policy else 0.5, step=0.05)
        minimum_rr = third.number_input("Minimum R:R", min_value=0.01, value=float(policy.minimum_rr) if policy else 1.5, step=0.1)
        st.markdown("**Hard limits**")
        first, second, third, fourth = st.columns(4)
        daily = first.number_input("Daily loss limit (R)", min_value=0.01, value=float(policy.daily_loss_limit_r) if policy else 2.0, step=0.25)
        weekly = second.number_input("Weekly loss limit (R)", min_value=0.01, value=float(policy.weekly_loss_limit_r) if policy else 4.0, step=0.25)
        drawdown = third.number_input("Maximum drawdown (%)", min_value=0.01, value=float(policy.max_drawdown_percent) if policy else 10.0, step=0.5)
        streak = fourth.number_input("Maximum loss streak", min_value=1, value=policy.max_consecutive_losses if policy else 3, step=1)
        with st.expander("Reference-only open-risk controls"):
            open_risk = st.number_input("Maximum open risk (R)", min_value=0.01, value=float(policy.max_open_risk_r) if policy else 1.0, step=0.25)
            correlation = st.text_area("Correlation / exposure policy", value=policy.correlation_policy if policy and policy.correlation_policy else "")
            st.caption("The closed-trade MT5 exporter cannot verify open risk or correlation exposure automatically.")
        submitted = st.form_submit_button("Save risk policy", type="primary")
    if submitted:
        try:
            repo.save_account_risk_policy(
                account_id=account.id, standard_risk_per_trade_percent=str(standard), maximum_risk_per_trade_percent=str(maximum),
                daily_loss_limit_r=str(daily), weekly_loss_limit_r=str(weekly), max_drawdown_percent=str(drawdown),
                max_open_risk_r=str(open_risk), max_consecutive_losses=int(streak), minimum_rr=str(minimum_rr), correlation_policy=correlation,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Risk policy saved as a new version.")
            st.rerun()


def _render_framework_rules(repo: SQLiteJournalRepository) -> None:
    settings = repo.get_framework_rule_settings()
    st.markdown("#### Framework rules")
    st.caption("These rules affect journal scores and alerts only. They never lock MT5 trading.")
    with st.form("framework-rule-settings"):
        revenge = st.checkbox("Oversized revenge trade is a hard Psychology and Risk failure", value=settings.oversized_revenge_hard)
        setup = st.checkbox("Mandatory setup absent is a hard System failure", value=settings.mandatory_setup_hard)
        stop = st.checkbox("Deliberately widened stop is a hard Risk failure", value=settings.stop_widened_hard)
        shutdown = st.checkbox("Trading after a hard shutdown is a hard Risk failure", value=settings.shutdown_breach_hard)
        threshold = st.number_input("Repeated critical violations before numeric cap", min_value=2, value=settings.repeated_critical_threshold, step=1)
        submitted = st.form_submit_button("Save framework rules", type="primary")
    if submitted:
        try:
            repo.save_framework_rule_settings(
                oversized_revenge_hard=revenge, mandatory_setup_hard=setup, stop_widened_hard=stop,
                shutdown_breach_hard=shutdown, repeated_critical_threshold=int(threshold),
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Framework rules saved.")
            st.rerun()
