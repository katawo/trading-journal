"""Native Streamlit presentation for the greenfield three-pillar framework."""

from __future__ import annotations

from collections import Counter
from collections.abc import MutableMapping, Sequence
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
    PillarRoadmapEvidenceView,
    SQLiteJournalRepository,
)
from trading_journal.presentation.i18n import tr


GRADE_OPTIONS = ("Pass", "Partial", "Fail")
REVIEW_PAGE_SIZE = 25
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
    return tr({"clear": "Clear", "caution": "Caution", "stop": "Stop", "unconfigured": "Set up"}[snapshot.state])


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
    return tr("Process failed — hard-rule event: {events}", events=", ".join(tr(item) for item in (assessed_hard_rules or ["recorded"])))


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


def _render_score_cards(scores: tuple[PillarScore, ...]) -> None:
    with st.container(horizontal=True, gap="small"):
        for score in scores:
            label = "FAIL" if score.hard_block else "Incomplete" if score.score is None else score.status.capitalize()
            st.metric(tr(PILLAR_NAMES[score.pillar]), _score_text(score.score), tr("{label} · {count} in sample", label=tr(label), count=score.sample_size), border=True)


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
    st.markdown("#### Three-pillar monitor")
    st.caption(tr("Psychology and System are trader-wide. Risk is scoped to {account}.", account=_account_label(account)))
    _render_risk_configuration_notice(service, account.id)
    with st.container(horizontal=True, gap="small"):
        st.metric("Readiness", _score_text(readiness.score), readiness.status.capitalize(), border=True)
        st.metric("Risk state", _state_label(snapshot), border=True)
        st.metric("Today", "—" if snapshot.daily_r is None else f"{Decimal(snapshot.daily_r):+.2f}R", border=True)
        st.metric("Max drawdown", "—" if snapshot.max_drawdown_percent is None else f"{Decimal(snapshot.max_drawdown_percent):.2f}%", border=True)
    _render_score_cards(scores)
    st.caption(tr(readiness.detail))


def render_framework_page(repo: SQLiteJournalRepository) -> None:
    st.markdown('<div class="dashboard-kicker">POST-TRADE JOURNAL</div>', unsafe_allow_html=True)
    st.subheader("Three-pillar framework")
    st.caption("Use completed MT5 trades to assess execution. Alerts are advisory; this journal never sends, blocks, or changes MT5 orders.")
    account = _select_account(repo, key="framework-account")
    if account is None:
        return
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
    trade_scores = {item.trade_id: item for item in score_items}
    _render_review_register(repo, account, trades, trade_scores, profiles)


def _clear_review_dialog() -> None:
    st.session_state.pop("post-trade-review-trade-id", None)


def _clear_group_dialog() -> None:
    st.session_state.pop("logical-trade-group-editor", None)
    st.session_state.pop("logical-trade-regroup-confirmation", None)


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
    with st.container(horizontal=True, gap="small"):
        st.metric("Symbol", trade.symbol, border=True)
        st.metric("Positions", str(trade.position_count), border=True)
        st.metric(f"P&L ({account.account_currency})", f"{Decimal(trade.net_pnl):+.2f}", border=True)
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
                            "P&L": Decimal(member.net_pnl),
                        }
                        for member in trade.members
                    ]
                ),
                hide_index=True,
                width="stretch",
                column_config={"P&L": st.column_config.NumberColumn(format="%.2f")},
            )


def _grade_control(label: str, *, existing: str | None, key: str) -> str | None:
    default = existing.capitalize() if existing else None
    choice = st.segmented_control(label, GRADE_OPTIONS, format_func=tr, default=default, key=key, width="stretch")
    return None if choice is None else choice.casefold()


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
    st.caption(tr("Correct {trade}" if existing else "Review {trade}", trade=trade.display_label))
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
    default_id = existing.strategy_profile_id if existing else score.mapped_strategy.id if score.mapped_strategy else _default_strategy_id(repo)
    strategy_index = next((index for index, item in enumerate(profiles) if item.id == default_id), 0)
    st.markdown("##### Assessment")
    with st.form(f"post-trade-assessment-{trade.id}"):
        strategy = st.selectbox("Strategy", profiles, index=strategy_index, format_func=lambda item: item.name)
        pillars = (
            ("Psychology", PSYCHOLOGY_CRITERIA),
            ("Risk management", RISK_CRITERIA),
            ("Trading system", SYSTEM_CRITERIA),
        )
        summaries = [_grade_summary(trade.id, criteria, existing.criterion_grades if existing else None) for _, criteria in pillars]
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
        grades: dict[str, str | None] = {}
        tabs = st.tabs([f"{tr(title)} · {done}/{len(criteria)}" for (title, criteria), (done, _) in zip(pillars, summaries, strict=True)])
        for tab, (title, criteria) in zip(tabs, pillars, strict=True):
            with tab:
                st.form_submit_button(
                    tr("Mark {pillar} as Pass", pillar=tr(title)),
                    key=f"assessment-{trade.id}-{title}-pass-all",
                    icon=":material/done_all:",
                    on_click=_set_pillar_grades_to_pass,
                    args=(trade.id, criteria),
                )
                st.caption("Change only the criteria that were Partial or Fail.")
                for criterion in criteria:
                    grades[criterion] = _grade_control(
                        CRITERION_LABELS[criterion],
                        existing=existing.criterion_grades.get(criterion) if existing else None,
                        key=f"assessment-{trade.id}-{criterion}",
                    )
        actual_risk = st.text_input(
            "Actual risk amount (optional)",
            value=existing.declared_actual_risk_amount if existing and existing.declared_actual_risk_amount else "",
            placeholder="Enter a verified amount when automatic evidence is not sufficient",
            help="Overrides automatic evidence for this logical trade's policy comparison. It does not rewrite immutable MT5 position history or account-limit monitoring.",
        )
        if policy is not None:
            st.caption(f"Risk policy v{policy.version}: Standard 1R {policy.standard_risk_per_trade_percent}% · maximum {policy.maximum_risk_per_trade_percent}%.")
        else:
            st.caption("No active Risk policy is attached; the assessment still records your judgement, while automatic limit checks remain unavailable.")
        violation_codes = st.multiselect(
            "Reason tags",
            options=list(VIOLATION_LABELS),
            default=list(existing.violation_codes) if existing else [],
            format_func=lambda code: tr(VIOLATION_LABELS[code]),
            help="Tag the cause of a partial or failed assessment so period reviews can identify recurring issues.",
        )
        hard_rules = st.multiselect(
            "Hard-rule events",
            options=available_hard_rules,
            default=list(existing.hard_rule_codes) if existing else [],
            format_func=lambda code: tr(HARD_RULE_LABELS[code]),
            help="Enabled events selected on save set Hard-rule status to Fail. That result is snapshotted for this assessment, so later Review rules changes do not rewrite it. Automatic Risk limits are monitoring evidence, not hard failures by themselves.",
        )
        if not available_hard_rules:
            st.caption("No hard-rule events are enabled. Enable one in Settings → Review rules to record it on a new assessment.")
        st.markdown("##### Review details")
        note = st.text_area("What happened and what did you learn?", value=existing.post_review_note if existing else "", placeholder="Describe execution independently of P&L.")
        action = st.text_area("Corrective action", value=existing.corrective_action if existing and existing.corrective_action else "", placeholder="Required when any criterion is Partial or Fail, or a hard rule is selected.")
        submitted = st.form_submit_button("Update assessment" if existing else "Save assessment", type="primary")
    if not submitted:
        _render_review_history(repo, account.id, trade)
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
            st.markdown(f"**Version {revision.version}** · {revision.archived_at[:19]} · {revision.strategy_snapshot.name}")
            st.caption(f"{failed} failed criterion/criteria · Hard rules: {', '.join(revision.hard_rule_codes) or 'none'}")
            st.write(revision.post_review_note)
        for assessment in superseded:
            positions = ", ".join(f"#{position_id}" for position_id in assessment.assessed_position_ids)
            st.markdown(f"**Superseded assessment** · {assessment.superseded_at[:19] if assessment.superseded_at else '—'} · {assessment.assessed_trade_label}")
            st.caption(f"Assessed {positions} · {assessment.superseded_reason or 'Logical-trade membership changed'}")
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
            f"{Decimal(position.net_pnl):+.2f} · "
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
        st.error(f"{count} saved assessment(s) will be superseded and removed from active scores, reports, alerts, and roadmap evidence. Each resulting logical trade needs a new review.")
        for label in confirmation["affected_assessment_labels"]:
            st.caption(f"Supersede: {label}")
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
    _defer_logical_trade_selection_reset(account.id)
    _clear_group_dialog()
    st.rerun()


def _render_review_register(repo: SQLiteJournalRepository, account: AccountListItem, trades, scores: dict[int, TradeProcessScore], profiles) -> None:  # type: ignore[no-untyped-def]
    ordered = sorted(trades, key=lambda item: (item.exit_time, item.id), reverse=True)
    groups = {
        "needs_approval": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].review_kind == "needs_approval"],
        "auto_reviewed": [
            (trade, scores[trade.id])
            for trade in ordered
            if scores[trade.id].review_kind in {"auto_review", "approved_auto_review"}
        ],
        "manual_reviewed": [(trade, scores[trade.id]) for trade in ordered if scores[trade.id].review_kind == "manual_review"],
        "all": [(trade, scores[trade.id]) for trade in ordered],
    }
    _prepare_logical_trade_register_state(account.id)
    filter_names = {
        "needs_approval": "Needs approval",
        "auto_reviewed": "Auto-reviewed",
        "manual_reviewed": "Manually reviewed",
        "all": "All",
    }
    filter_labels = {key: f"{filter_names[key]} ({len(items)})" for key, items in groups.items()}
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
    single_position_trades = [trade for trade, _ in visible if not trade.is_group]
    single_position_by_id = {trade.id: trade for trade in single_position_trades}
    selected_logical_trade_ids = tuple(
        trade_id
        for trade_id in st.session_state.get(_logical_trade_selection_store_key(account.id), ())
        if trade_id in single_position_by_id
    )
    st.session_state[_logical_trade_selection_store_key(account.id)] = selected_logical_trade_ids
    selected_position_trade_ids = tuple(
        single_position_by_id[trade_id].members[0].id for trade_id in selected_logical_trade_ids
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
            disabled=not selected_position_trade_ids,
            on_click=_clear_logical_trade_selection,
            args=(account.id,),
        )
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
        qualifier = " failed" if failed_only else ""
        st.info(f"No {filter_names[selected_group].casefold()}{qualifier} trades for this account.")
    else:
        start = (current_page - 1) * REVIEW_PAGE_SIZE
        page_items = visible[start : start + REVIEW_PAGE_SIZE]
        header = st.columns([0.5, 0.85, 1.35, 1.45, 0.75, 1.1, 0.7, 0.75, 1.0])
        for column, label in zip(
            header,
            ("Select", "Logical trade", "Trade", "Positions", "P&L", "Review", "Score", "Hard rules", "Actions"),
            strict=True,
        ):
            column.caption(tr(label))
        for trade, score in page_items:
            review = {
                "needs_approval": "Needs approval",
                "auto_review": "Auto-review",
                "approved_auto_review": "Approved auto-review",
                "manual_review": "Reviewed",
            }.get(score.review_kind, "Needs approval")
            with st.container(border=True):
                select_column, logical_column, trade_column, positions_column, pnl_column, review_column, score_column, process_column, actions_column = st.columns(
                    [0.5, 0.85, 1.35, 1.45, 0.75, 1.1, 0.7, 0.75, 1.0]
                )
                if trade.is_group:
                    select_column.caption("—")
                else:
                    checkbox_key = f"{_logical_trade_selection_prefix(account.id)}{trade.id}"
                    if checkbox_key not in st.session_state:
                        st.session_state[checkbox_key] = trade.id in selected_logical_trade_ids
                    select_column.checkbox(
                        f"Select LT-{trade.id}",
                        key=checkbox_key,
                        label_visibility="collapsed",
                        help="Select this single-position logical trade to group it with other selected trades.",
                        on_change=_toggle_logical_trade_selection,
                        args=(account.id, trade.id),
                    )
                logical_column.markdown(f"**LT-{trade.id}**")
                trade_column.write(trade.display_label)
                positions_column.write(", ".join(f"#{position_id}" for position_id in trade.position_ids))
                pnl_column.write(f"{Decimal(trade.net_pnl):+.2f}")
                review_column.write(tr(review))
                score_column.markdown(f"**{_score_text(score.overall_score)}**")
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
                    st.rerun()
                if score.review_kind == "needs_approval" and actions_column.button(
                    "Approve", key=f"approve-auto-review-{account.id}-{trade.id}", type="tertiary", icon=":material/check:",
                ):
                    try:
                        repo.approve_auto_review(
                            account_id=account.id, trade_id=trade.id, risk_policy_id=None,
                            risk_evidence_source=score.risk_evidence_source,
                            risk_policy_state=score.risk_policy_state,
                            actual_risk_amount=score.actual_risk_amount,
                            criterion_grades=FrameworkService._automatic_review_grades(score.risk_policy_state),
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.session_state["post-trade-review-notice"] = "Automatic risk evidence approved."
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
                    f"· {_auto_risk_label(score)} · {tr(score.classification or 'Unclassified')} "
                    f"· {tr('Psychology')} {_score_text(score.psychology_score)} · {tr('Risk management')} {_score_text(score.risk_score)} · {tr('Trading system')} {_score_text(score.system_score)}"
                )
                st.caption(summary)
                if failure_detail := _process_failure_detail(score):
                    st.caption(failure_detail)
                if monitoring_detail := _automatic_risk_monitoring_detail(score):
                    st.caption(monitoring_detail)
        st.caption("Within-policy automatic evidence is counted as an Auto-review. Approval-needed evidence can be approved here or replaced by a full assessment.")
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
    st.markdown("#### Monitoring")
    _render_risk_configuration_notice(service, account.id)
    window = st.segmented_control("Rolling sample", [20, 30, 50], default=20, required=True, width="content", key=f"framework-window-{account.id}")
    scores = service.pillar_scores(account.id, window=int(window))
    readiness = service.readiness(account.id, window=int(window))
    st.metric("Overall readiness", _score_text(readiness.score), readiness.status.capitalize(), border=True)
    st.caption(readiness.detail)
    _render_score_cards(scores)
    component_rows = [
        {tr("Pillar"): tr(PILLAR_NAMES[score.pillar]), tr("Metric"): tr(name), tr("Score"): _score_text(value), tr("Scope"): tr(score.scope)}
        for score in scores for name, value in score.component_scores
    ]
    if component_rows:
        st.markdown("##### What drives the current scores")
        st.dataframe(pd.DataFrame(component_rows), hide_index=True, width="stretch")
    trend = service.rolling_score_trend(account.id, window=int(window))
    if trend:
        st.markdown("##### Selected-account execution trend")
        frame = pd.DataFrame(trend, columns=[tr("Closed"), tr("Psychology"), tr("Risk management"), tr("Trading system")]).set_index(tr("Closed"))
        st.line_chart(frame, width="stretch")
        st.caption("This trend uses selected-account reviewed trades. Score-card scopes remain explicit above.")
    classifications = Counter(score.classification for score in service.trade_process_scores(account.id) if score.classification is not None)
    issues = service.recurring_issues(account.id, window=int(window))
    left, right = st.columns(2)
    with left:
        st.markdown("##### Process-quality distribution")
        if classifications:
            st.bar_chart(pd.DataFrame({tr("Classification"): [tr(item) for item in classifications], tr("Trades"): list(classifications.values())}).set_index(tr("Classification")), width="stretch")
        else:
            st.caption("Save complete assessments to build a process-quality distribution.")
    with right:
        st.markdown("##### Recurring issues")
        if issues:
            st.dataframe(pd.DataFrame([{tr("Issue"): tr(VIOLATION_LABELS.get(issue, issue)), tr("Count"): count} for issue, count in issues]), hide_index=True, width="stretch")
        else:
            st.caption("No tagged recurring issues in this sample.")
    _render_period_reviews(repo, account, service)


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
            st.caption(f"{tr(latest.cadence.capitalize())} · {latest.period_start} to {latest.period_end} · {tr('Readiness').casefold()} {_score_text(latest.readiness_score)}")
            st.write(latest.review_note)
            st.markdown(f"**Priority action:** {latest.priority_action}")


def _next_roadmap_item(
    pillar: str,
    evidence: dict[tuple[str, int, str], PillarRoadmapEvidenceView],
) -> tuple[int, str, str] | None:
    """Return the first incomplete saved-evidence item for a roadmap pillar."""
    for level, items in ROADMAP_ITEMS[pillar].items():
        for item_key, label in items:
            item = evidence.get((pillar, level, item_key))
            if item is None or not item.completed:
                return level, item_key, label
    return None


def _render_roadmap(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    statuses = {item.pillar: item for item in service.roadmap_status(account.id)}
    evidence = {(item.pillar, item.level, item.item_key): item for item in repo.list_pillar_roadmap_evidence(account.id)}
    st.markdown("#### Readiness roadmap")
    st.caption("Complete the next evidence item for each pillar. Score and review gates unlock automatically when met.")
    for pillar, name in PILLAR_NAMES.items():
        status = statuses[pillar]
        next_item = _next_roadmap_item(pillar, evidence)
        with st.container(border=True):
            st.markdown(f"##### {tr(name)}")
            st.caption(f"Level {status.current_level} · {status.completed_items}/{status.total_items} evidence complete")
            if next_item is None:
                st.success("All roadmap evidence is complete. Continue monitoring the current sample.", icon=":material/check_circle:")
                continue
            level, item_key, label = next_item
            st.markdown(f"**{tr('Next:')}** {tr(label)}")
            if not status.can_complete_current_level:
                st.info(status.gate, icon=":material/lock:")
                continue
            existing = evidence.get((pillar, level, item_key))
            with st.form(f"roadmap-next-{pillar}-{level}-{item_key}"):
                complete = st.checkbox("I completed this step", value=bool(existing and existing.completed))
                note = st.text_area(
                    "Evidence note",
                    value=existing.evidence_note if existing and existing.evidence_note else "",
                    placeholder="Briefly record the evidence for this step.",
                )
                submitted = st.form_submit_button("Mark complete", type="primary")
            if submitted:
                if not complete:
                    st.warning("Confirm completion before saving this roadmap item.")
                else:
                    try:
                        repo.save_pillar_roadmap_evidence(
                            account_id=account.id,
                            pillar=pillar,
                            level=level,
                            item_key=item_key,
                            completed=True,
                            evidence_note=note,
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success(tr("{name} roadmap item completed.", name=tr(name)))
                        st.rerun()
    completed = [item for item in evidence.values() if item.completed]
    if completed:
        with st.expander("Completed evidence", icon=":material/history:"):
            for pillar, name in PILLAR_NAMES.items():
                pillar_items = sorted((item for item in completed if item.pillar == pillar), key=lambda item: (item.level, item.item_key))
                if not pillar_items:
                    continue
                st.markdown(f"**{tr(name)}**")
                labels = {
                    (level, item_key): label
                    for level, items in ROADMAP_ITEMS[pillar].items()
                    for item_key, label in items
                }
                for item in pillar_items:
                    label = labels[(item.level, item.item_key)]
                    detail = f" — {item.evidence_note}" if item.evidence_note else ""
                    st.markdown(f"- {tr('Level')} {item.level}: {tr(label)}{detail}")


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
            repo.save_account_risk_policy(
                account_id=account.id, standard_risk_per_trade_percent=str(standard), maximum_risk_per_trade_percent=str(maximum),
                daily_loss_limit_r=str(daily), weekly_loss_limit_r=str(weekly), max_drawdown_percent=str(drawdown),
                max_open_risk_r=str(open_risk), max_consecutive_losses=int(streak), minimum_rr=str(minimum_rr), correlation_policy=correlation,
                pretrade_balance_auto_evidence_enabled=pretrade_balance_evidence,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Risk policy saved as a new version.")
            st.rerun()


def _render_framework_rules(repo: SQLiteJournalRepository) -> None:
    settings = repo.get_framework_rule_settings()
    st.markdown("#### Review rules")
    st.caption("These rules affect new or corrected assessments and alerts only. Their effective result is snapshotted when an assessment is saved; later changes never rewrite history or lock MT5 trading.")
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
            st.success("Review rules saved.")
            st.rerun()
