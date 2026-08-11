"""Streamlit views for post-trade three-pillar reviews of imported MT5 positions."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from trading_journal.application.framework import PILLAR_NAMES, ROADMAP_ITEMS, FrameworkService, PillarScore, RiskSnapshot, TradeProcessScore
from trading_journal.infrastructure.sqlite_repository import AccountListItem, AccountRiskPolicyView, SQLiteJournalRepository


SYSTEM_FAILURE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("market_context", "Market context"),
    ("session", "Trading session"),
    ("timeframe", "Timeframe"),
    ("regime", "Market regime"),
    ("location", "Location"),
    ("confirmation", "Confirmation"),
    ("entry_trigger", "Entry trigger"),
    ("invalidation", "Invalidation"),
    ("target", "Target"),
)


def _account_label(account: AccountListItem) -> str:
    return f"{account.display_name} · {account.login} · {account.broker_server}"


def _select_account(repo: SQLiteJournalRepository, *, key: str) -> AccountListItem | None:
    accounts = repo.list_mt5_accounts()
    if not accounts:
        st.info("Add an approved MT5 account in Settings before using the framework.")
        return None
    return st.selectbox("Trading account", accounts, format_func=_account_label, key=key)


def _state_label(snapshot: RiskSnapshot) -> str:
    return {"clear": "CLEAR", "caution": "CAUTION", "stop": "STOP", "unconfigured": "SET UP"}[snapshot.state]


def _render_risk_state(snapshot: RiskSnapshot) -> None:
    st.markdown(
        f'<div class="framework-status framework-status--{snapshot.state}">'
        f"<strong>{_state_label(snapshot)}</strong><span>{snapshot.message}</span></div>",
        unsafe_allow_html=True,
    )


def _score_text(score: PillarScore) -> str:
    return "—" if score.score is None else f"{Decimal(score.score):.0f}%"


def _trade_score_text(value: str | None) -> str:
    return "—" if value is None else f"{Decimal(value):.0f}%"


def _risk_source_label(risk_basis: str) -> str:
    return {
        "specific_preset_sl": "Specific preset SL",
        "real_loss_sl": "Real-loss SL",
        "live_account_balance_sl": "Live-account-balance SL",
        "unavailable": "Risk source",
    }[risk_basis]


def _auto_risk_label(state: str, risk_basis: str = "unavailable") -> str:
    label = {"within_policy": "Within policy", "over_policy": "Over policy", "unavailable": "Unavailable"}[state]
    return f"{_risk_source_label(risk_basis)} · {label}" if risk_basis != "unavailable" else label


def _stop_evidence_label(value: bool | None) -> str:
    return "Observed wider" if value else "Not observed at close" if value is not None else "Unavailable"


def _render_score_cards(scores: tuple[PillarScore, ...]) -> None:
    columns = st.columns(3)
    for column, score in zip(columns, scores, strict=True):
        state = (
            "Awaiting imports"
            if score.score is None and score.unreviewed_total == 0 and score.auto_reviewed_total == 0
            else "Risk auto-reviewed"
            if score.score is None and score.auto_reviewed_total
            else "Needs review"
            if score.score is None
            else "Review needed"
            if score.hard_block
            else "Needs review"
            if score.unreviewed_count
            else "Early evidence"
            if score.reviewed_total < 10
            else "On track"
        )
        evidence = f"{score.reviewed_count} reviewed · {score.unreviewed_count} awaiting"
        if score.auto_reviewed_count:
            evidence += f" · {score.auto_reviewed_count} auto-reviewed"
        column.metric(PILLAR_NAMES[score.pillar], _score_text(score), f"{state} · {evidence}", border=True)
        column.caption(score.scope)
        column.caption(score.detail)


def render_framework_dashboard(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    """Compact, read-only process status embedded in the main dashboard."""
    st.markdown("#### Three-pillar review status")
    st.caption(f"For {account.display_name} · {account.login} · {account.broker_server}")
    service = FrameworkService(repo)
    snapshot = service.risk_snapshot(account.id)
    scores = service.pillar_scores(account.id)
    risk_score = next(item for item in scores if item.pillar == "risk")
    automatic_risk = service.trade_process_scores(account.id)[-20:]
    risk_passed = sum(item.auto_risk.state == "within_policy" for item in automatic_risk)
    risk_breaches = sum(item.auto_risk.state == "over_policy" for item in automatic_risk)
    with st.container(horizontal=True, gap="small"):
        st.metric("Risk state", _state_label(snapshot), border=True)
        st.metric("Today", "—" if snapshot.daily_r is None else f"{Decimal(snapshot.daily_r):+.2f}R", border=True)
        st.metric("This week", "—" if snapshot.weekly_r is None else f"{Decimal(snapshot.weekly_r):+.2f}R", border=True)
        st.metric("Maximum drawdown", "—" if snapshot.max_drawdown_percent is None else f"{Decimal(snapshot.max_drawdown_percent):.2f}%", border=True)
        st.metric("Automatic risk", f"{risk_passed} pass · {risk_breaches} alert", border=True)
    if risk_breaches:
        st.warning(f"{risk_breaches} recent imported trade(s) exceeded their saved Risk-policy amount. Review the MT5 evidence; this advisory does not control MT5.")
    _render_score_cards(scores)
    st.caption("MT5 facts can flag Risk and map a strategy, but Psychology and setup validity require a post-trade review. Specific preset, Real-loss, and Live-account-balance SL sources are Risk-only auto-reviews.")


def render_framework_page(repo: SQLiteJournalRepository) -> None:
    st.markdown('<div class="dashboard-kicker">POST-TRADE JOURNAL</div>', unsafe_allow_html=True)
    st.subheader("Three-pillar framework")
    st.caption("Review completed MT5 positions after they close. The journal never approves, blocks, or sends a trade to MT5.")
    account = _select_account(repo, key="framework-account")
    if account is None:
        return

    review_tab, roadmap_tab, policy_tab = st.tabs(["Review closed trades", "Roadmap", "Risk policy"])
    with review_tab:
        _render_post_trade_review(repo, account)
    with roadmap_tab:
        _render_roadmap(repo, account)
    with policy_tab:
        _render_risk_policy(repo, account)


def _render_post_trade_review(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    snapshot = service.risk_snapshot(account.id)
    _render_risk_state(snapshot)
    notice = st.session_state.pop("post-trade-review-notice", None)
    if notice:
        st.success(notice, icon=":material/check_circle:")

    trades = repo.list_closed_trades_for_review(account.id)
    if not trades:
        st.info("No completed MT5 positions have been imported for this account yet.")
        return
    trade_scores = {item.trade_id: item for item in service.trade_process_scores(account.id)}
    profiles = repo.list_strategy_profiles()
    if not profiles:
        st.warning("Create a documented strategy in Settings → Strategies before saving a three-pillar review.", icon=":material/info:")
    _render_review_register(repo, account, trades, trade_scores, profiles)


def _clear_review_register_selection() -> None:
    """Clear the focused trade when a review dialog closes or saves."""
    st.session_state.pop("post-trade-review-trade-id", None)


def _render_imported_execution(account: AccountListItem, trade, process_score: TradeProcessScore) -> None:  # type: ignore[no-untyped-def]
    st.markdown("##### Imported execution")
    first, second, third, fourth = st.columns(4)
    first.metric("Symbol", trade.symbol, border=True)
    second.metric("Position", f"#{trade.position_id or '—'}", border=True)
    third.metric("Volume", trade.volume, border=True)
    fourth.metric(f"P&L ({account.account_currency})", f"{Decimal(trade.net_pnl):+.2f}", border=True)
    st.caption(f"Entry {trade.entry_time} at {trade.entry_price} · Closed {trade.exit_time} at {trade.exit_price}. These MT5 values are read-only.")
    if process_score.assessment_state == "reviewed":
        st.caption(
            f"Reviewed Process score: {_trade_score_text(process_score.overall_score)} "
            f"· Psychology {_trade_score_text(process_score.psychology_score)} "
            f"· Risk {_trade_score_text(process_score.risk_score)} "
            f"· System {_trade_score_text(process_score.system_score)}"
        )
    elif process_score.assessment_state == "auto_reviewed":
        st.info(
            f"{_risk_source_label(process_score.auto_risk.risk_basis)} auto-review: Risk {_trade_score_text(process_score.risk_score)}. "
            "Psychology, Trading System, Process score, and roadmap progress still need a full post-trade review.",
            icon=":material/auto_awesome:",
        )
    else:
        st.caption("Not scored. Save a full post-trade review to record Psychology, Risk, Trading System, and Process scores.")
    automatic = process_score.auto_risk
    facts = st.columns(4)
    facts[0].metric("Automatic risk", _auto_risk_label(automatic.state, automatic.risk_basis), border=True)
    facts[1].metric(_risk_source_label(automatic.risk_basis), automatic.source_amount or "—", border=True)
    facts[2].metric("Initial R:R", "—" if automatic.initial_rr is None else f"{Decimal(automatic.initial_rr):.2f}", border=True)
    facts[3].metric("Stop evidence", _stop_evidence_label(automatic.observed_stop_widened), border=True)
    st.caption(automatic.detail)
    strategy_mapping = "Unmapped"
    if process_score.mapped_strategy is not None:
        strategy_mapping = f"Mapped to {process_score.mapped_strategy.name}"
    st.caption(f"MT5 strategy mapping: {strategy_mapping} · Exit reason: {trade.exit_reason or 'unavailable'}.")


@st.dialog("Post-trade review", width="large", on_dismiss=_clear_review_register_selection)
def _render_post_trade_review_dialog(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    trade,
    process_score: TradeProcessScore,
    profiles,
) -> None:  # type: ignore[no-untyped-def]
    existing = repo.get_post_trade_assessment_for_trade(trade.id)
    st.caption(f"{'Edit' if existing else 'Review'} #{trade.position_id or '—'} · {trade.symbol}")
    with st.container(border=True):
        _render_imported_execution(account, trade, process_score)

    if not profiles:
        st.warning("Create a documented strategy in Settings → Strategies before this trade can be reviewed.", icon=":material/info:")
        return

    policy = repo.get_active_risk_policy(account.id)
    default_strategy_id = existing.strategy_profile_id if existing else process_score.mapped_strategy.id if process_score.mapped_strategy else _default_strategy_id(repo)
    strategy_index = next((index for index, item in enumerate(profiles) if item.id == default_strategy_id), 0)

    st.markdown("##### Three-pillar assessment")
    with st.form(f"post-trade-assessment-{trade.id}"):
        system, risk, psychology = st.columns(3)
        with system:
            st.markdown("**Trading system**")
            strategy = st.selectbox("Strategy", profiles, index=strategy_index, format_func=lambda item: item.name)
            system_confirmed = st.checkbox(
                "Valid documented setup",
                value=existing.system_confirmed if existing else True,
                help="Judge the completed trade against the strategy rules, not its profit or loss.",
            )
            selected_failures = []
            if not system_confirmed:
                selected_failures = st.multiselect(
                    "Failed criteria",
                    options=[key for key, _ in SYSTEM_FAILURE_OPTIONS],
                    default=list(existing.system_failure_codes) if existing else [],
                    format_func=dict(SYSTEM_FAILURE_OPTIONS).get,
                )
        with risk:
            st.markdown("**Risk management**")
            actual_risk = st.text_input(
                "Actual risk amount (optional)",
                value=existing.declared_actual_risk_amount if existing and existing.declared_actual_risk_amount else "",
                placeholder="e.g. 10.00",
                help="Overrides Specific preset SL, Real-loss SL, or Live-account-balance SL. Leave blank to use the automatic source when one is available, otherwise the attached policy's standard 1R.",
            )
            stop_widened = st.checkbox(
                "Moved stop farther / widened risk",
                value=existing.stop_widened_violation if existing else bool(process_score.auto_risk.observed_stop_widened),
                help="MT5 can flag a wider stop recorded at close, but cannot prove every intratrade stop change.",
            )
            if policy is None:
                st.caption("No risk policy is attached yet. Save one in the Risk policy tab to score risk compliance.")
            else:
                st.caption(
                    f"Policy v{policy.version}: standard 1R {policy.standard_risk_per_trade_percent}% · "
                    f"maximum per trade {policy.maximum_risk_per_trade_percent}%."
                )
        with psychology:
            st.markdown("**Psychology**")
            impulse = st.checkbox("Impulse entry or management", value=existing.impulse_violation if existing else False)
            revenge = st.checkbox("Revenge trade", value=existing.revenge_violation if existing else False)
            emotional_size = st.checkbox("Emotionally increased size", value=existing.emotional_size_violation if existing else False)
        review_note = st.text_area(
            "What happened and what did you learn?",
            value=existing.post_review_note if existing else "",
            placeholder="Describe the execution and outcome without judging it only by P&L.",
        )
        corrective_action = st.text_area(
            "Corrective action (optional)",
            value=existing.corrective_action if existing and existing.corrective_action else "",
            placeholder="One specific adjustment for a repeated process mistake.",
        )
        submitted = st.form_submit_button("Update review" if existing else "Save review", type="primary")
        if existing:
            st.caption(f"Saving a correction archives version {existing.version}; the new review becomes version {existing.version + 1}.")
    if submitted:
        try:
            repo.save_post_trade_assessment(
                account_id=account.id,
                trade_id=trade.id,
                risk_policy_id=policy.id if policy else None,
                strategy_profile_id=strategy.id,
                system_confirmed=system_confirmed,
                system_failure_codes=tuple(selected_failures),
                impulse_violation=impulse,
                revenge_violation=revenge,
                emotional_size_violation=emotional_size,
                stop_widened_violation=stop_widened,
                declared_actual_risk_amount=actual_risk,
                post_review_note=review_note,
                corrective_action=corrective_action,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state["post-trade-review-notice"] = "Post-trade review saved."
            _clear_review_register_selection()
            st.rerun()

    revisions = repo.list_post_trade_assessment_revisions(trade.id)
    if revisions:
        with st.expander(f"Review history ({len(revisions)} archived version{'s' if len(revisions) != 1 else ''})"):
            for revision in revisions:
                status = "Valid setup" if revision.system_confirmed else "Invalid setup"
                breaches = sum(
                    [
                        revision.impulse_violation,
                        revision.revenge_violation,
                        revision.emotional_size_violation,
                        revision.stop_widened_violation,
                    ]
                )
                st.markdown(f"**Version {revision.version}** · {revision.archived_at[:19]} · {revision.strategy_snapshot.name}")
                st.caption(f"{status} · {breaches} recorded breach(es) · Actual risk: {revision.declared_actual_risk_amount or 'not recorded'}")
                st.write(revision.post_review_note)

def _default_strategy_id(repo: SQLiteJournalRepository) -> int | None:
    try:
        return repo.get_journal_settings().default_strategy_profile_id
    except RuntimeError:
        return None


def _render_review_register(
    repo: SQLiteJournalRepository,
    account: AccountListItem,
    trades,
    trade_scores: dict[int, TradeProcessScore],
    profiles,
) -> None:
    ordered_trades = sorted(trades, key=lambda item: (item.exit_time, item.id), reverse=True)
    reviewed = [(trade, trade_scores[trade.id]) for trade in ordered_trades if trade_scores[trade.id].assessment_state == "reviewed"]
    auto_reviewed = [(trade, trade_scores[trade.id]) for trade in ordered_trades if trade_scores[trade.id].assessment_state == "auto_reviewed"]
    needs_review = [
        (trade, trade_scores[trade.id])
        for trade in ordered_trades
        if trade_scores[trade.id].assessment_state == "not_scored"
    ]

    st.markdown("##### Review register")
    pending_metric, automatic_metric, reviewed_metric = st.columns(3)
    pending_metric.metric("Needs review", len(needs_review), border=True)
    automatic_metric.metric("Auto-reviewed", len(auto_reviewed), border=True)
    reviewed_metric.metric("Reviewed", len(reviewed), border=True)
    review_filter = st.segmented_control(
        "Review status",
        ["Needs review", "Auto-reviewed", "Reviewed", "All"],
        default="Needs review",
        required=True,
        key=f"review-filter-{account.id}",
        width="content",
    )
    visible_items = (
        needs_review
        if review_filter == "Needs review"
        else reviewed
        if review_filter == "Reviewed"
        else auto_reviewed
        if review_filter == "Auto-reviewed"
        else [(trade, trade_scores[trade.id]) for trade in ordered_trades]
    )
    if not visible_items:
        st.info(f"No {review_filter.lower()} trades for this account.")
        return

    rows = []
    for trade, score in visible_items:
        rows.append(
            {
                "Closed": trade.exit_time,
                "Position": f"#{trade.position_id or '—'}",
                "Symbol": trade.symbol,
                "P&L": trade.net_pnl,
                "Review": {
                    "not_scored": "Needs review",
                    "auto_reviewed": "Auto-reviewed",
                    "reviewed": "Reviewed",
                }[score.assessment_state],
                "Risk limit": score.policy_risk_amount or "—",
                "Actual risk": score.actual_risk_amount or "—",
                "Automatic risk": _auto_risk_label(score.auto_risk.state, score.auto_risk.risk_basis),
                "Risk score": _trade_score_text(score.risk_score),
                "Process score": _trade_score_text(score.overall_score),
                "Open": ":material/edit:",
            }
        )
    register_key = f"review-register-{account.id}-{review_filter.replace(' ', '-').lower()}"

    def open_review_from_register() -> None:
        click = st.session_state.get(register_key)
        if click is None or click["row"] >= len(visible_items):
            return
        st.session_state["post-trade-review-trade-id"] = visible_items[click["row"]][0].id

    st.dataframe(
        pd.DataFrame(rows),
        column_config={
            "Open": st.column_config.ButtonColumn(
                "",
                type="tertiary",
                help="Open post-trade review",
                on_click=open_review_from_register,
                key=register_key,
            )
        },
        hide_index=True,
        width="stretch",
    )
    st.caption(
        f"Amounts use {account.account_currency}. Risk limit is the attached policy's maximum per-trade amount; actual risk uses the saved review value when present, otherwise the available automatic Risk source. "
        "Use the pencil action to review or correct a trade. Auto-reviewed Specific preset, Real-loss, and Live-account-balance SL trades contain a Risk sizing check only; a saved full review is still required for three-pillar scores and roadmap evidence."
    )

    selected_trade_id = st.session_state.get("post-trade-review-trade-id")
    if selected_trade_id is None:
        return
    selected_item = next(((trade, score) for trade, score in visible_items if trade.id == selected_trade_id), None)
    if selected_item is None:
        st.session_state.pop("post-trade-review-trade-id", None)
        return
    trade, process_score = selected_item
    _render_post_trade_review_dialog(repo, account, trade, process_score, profiles)


def _render_roadmap(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    service = FrameworkService(repo)
    st.markdown("#### Parallel readiness roadmap")
    st.caption("The pillars advance in parallel. Complete evidence only for the next unlocked level.")
    scores = service.pillar_scores(account.id)
    evidence = {(item.pillar, item.level, item.item_key): item for item in repo.list_pillar_roadmap_evidence(account.id)}
    statuses = {item.pillar: item for item in service.roadmap_status(account.id)}
    score_by_pillar = {score.pillar: score for score in scores}
    overview = st.columns(3)
    for column, pillar in zip(overview, PILLAR_NAMES, strict=True):
        status = statuses[pillar]
        score = score_by_pillar[pillar]
        column.metric(PILLAR_NAMES[pillar], f"Level {status.current_level}", f"{status.completed_items}/{status.total_items} evidence", border=True)
        column.caption(_score_text(score) + " score · " + status.gate)

    pillar = st.segmented_control(
        "Roadmap pillar",
        list(PILLAR_NAMES),
        format_func=lambda item: PILLAR_NAMES[item],
        default="psychology",
        required=True,
        width="content",
        key=f"roadmap-pillar-{account.id}",
    )
    title = PILLAR_NAMES[pillar]
    status = statuses[pillar]
    current_items = ROADMAP_ITEMS[pillar][status.current_level]
    st.markdown(f"##### {title} · Level {status.current_level}")
    st.progress(status.completed_items / status.total_items, text=f"{status.completed_items} of {status.total_items} evidence items complete")
    if not status.can_complete_current_level:
        st.info(status.gate)
    else:
        st.caption(status.gate)
        with st.form(f"roadmap-{pillar}"):
            for item_key, label in current_items:
                existing = evidence.get((pillar, status.current_level, item_key))
                st.checkbox(label, value=bool(existing and existing.completed), key=f"roadmap-{pillar}-{status.current_level}-{item_key}")
                st.text_area(
                    "Evidence",
                    value=existing.evidence_note if existing and existing.evidence_note else "",
                    placeholder="What proves this?",
                    key=f"roadmap-note-{pillar}-{status.current_level}-{item_key}",
                )
            submitted = st.form_submit_button(f"Save {title} evidence", type="primary")
        if submitted:
            try:
                for item_key, _ in current_items:
                    repo.save_pillar_roadmap_evidence(
                        account_id=account.id,
                        pillar=pillar,
                        level=status.current_level,
                        item_key=item_key,
                        completed=st.session_state[f"roadmap-{pillar}-{status.current_level}-{item_key}"],
                        evidence_note=st.session_state[f"roadmap-note-{pillar}-{status.current_level}-{item_key}"],
                    )
            except ValueError as error:
                st.error(str(error))
            else:
                st.success(f"{title} roadmap evidence saved.")
                st.rerun()

    completed_items = [
        (level, label, item.evidence_note)
        for level, items in ROADMAP_ITEMS[pillar].items()
        for item_key, label in items
        if (item := evidence.get((pillar, level, item_key))) and item.completed
    ]
    if completed_items:
        with st.expander("Completed evidence"):
            for level, label, note in completed_items:
                st.markdown(f"**Level {level} · {label}**")
                st.caption(note or "No note recorded")

    with st.expander("Future levels"):
        for level, items in ROADMAP_ITEMS[pillar].items():
            if level > status.current_level:
                st.caption(f"Level {level}: " + " · ".join(label for _, label in items))


def _render_risk_policy(repo: SQLiteJournalRepository, account: AccountListItem) -> None:
    policy = repo.get_active_risk_policy(account.id)
    st.markdown("#### Account risk policy")
    funded_capital = repo.get_account_funded_capital(account.id)
    st.caption("This policy monitors completed MT5 positions after they close. It never sends, changes, or blocks an MT5 order.")
    if funded_capital is None:
        st.warning("Set funded capital in Settings → MT5 Accounts before saving this policy.")
    else:
        st.info(f"Funded capital: {funded_capital} {account.account_currency}. Updating it recalculates historical drawdown and Risk monitoring.")
    if policy is not None:
        st.info(f"Active version {policy.version}, saved {policy.created_at[:10]}. Existing post-trade reviews keep their policy reference.")
    with st.form("account-risk-policy"):
        st.markdown("**Core policy**")
        first, second, third = st.columns(3)
        standard_risk = first.number_input(
            "Standard risk (1R) (%)",
            min_value=0.01,
            max_value=100.0,
            value=float(policy.standard_risk_per_trade_percent) if policy else 0.5,
            step=0.05,
            help="Used to normalize dashboard R when a trade has no trade-specific risk evidence.",
        )
        maximum_risk = second.number_input(
            "Maximum risk per trade (%)",
            min_value=0.01,
            max_value=100.0,
            value=float(policy.maximum_risk_per_trade_percent) if policy else 0.5,
            step=0.05,
            help="Compliance threshold for automatic and saved Risk reviews.",
        )
        minimum_rr = third.number_input("Minimum R:R", min_value=0.01, value=float(policy.minimum_rr) if policy else 1.5, step=0.1)
        st.caption("Standard risk defines 1R for reporting. Maximum risk is the separate safety limit and cannot be lower than standard risk.")
        st.markdown("**Hard limits**")
        first, second, third, fourth = st.columns(4)
        daily_limit = first.number_input("Daily loss limit (R)", min_value=0.01, value=float(policy.daily_loss_limit_r) if policy else 2.0, step=0.25)
        weekly_limit = second.number_input("Weekly loss limit (R)", min_value=0.01, value=float(policy.weekly_loss_limit_r) if policy else 5.0, step=0.25)
        drawdown_limit = third.number_input("Maximum drawdown (%)", min_value=0.01, max_value=100.0, value=float(policy.max_drawdown_percent) if policy else 10.0, step=0.5)
        max_losses = fourth.number_input("Maximum loss streak", min_value=1, value=policy.max_consecutive_losses if policy else 3, step=1)
        with st.expander("Advanced controls"):
            max_open_risk = st.number_input("Maximum open risk (R)", min_value=0.01, value=float(policy.max_open_risk_r) if policy else 1.0, step=0.25)
            st.caption("Reference only: the MT5 bridge exports completed positions, so open-position risk cannot be monitored here.")
            correlation_policy = st.text_area("Correlation / exposure policy", value=policy.correlation_policy or "" if policy else "", placeholder="e.g. XAUUSD and USDJPY count as correlated USD exposure.")
        submitted = st.form_submit_button("Save risk policy", type="primary")
    if submitted:
        try:
            repo.save_account_risk_policy(
                account_id=account.id,
                standard_risk_per_trade_percent=str(standard_risk),
                maximum_risk_per_trade_percent=str(maximum_risk),
                daily_loss_limit_r=str(daily_limit),
                weekly_loss_limit_r=str(weekly_limit),
                max_drawdown_percent=str(drawdown_limit),
                max_open_risk_r=str(max_open_risk),
                max_consecutive_losses=int(max_losses),
                minimum_rr=str(minimum_rr),
                correlation_policy=correlation_policy,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Account risk policy saved as a new version.")
            st.rerun()
