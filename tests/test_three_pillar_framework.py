from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import sqlite3

import pytest

import streamlit as st

from trading_journal.application.framework import FrameworkService, ROADMAP_ITEMS, ReadinessAssessment, RiskSnapshot
from trading_journal.application.dashboard import DashboardService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import (
    ASSESSMENT_CRITERIA,
    JournalDatabaseResetRequiredError,
    ReviewContextSelection,
    SQLiteJournalRepository,
)
from trading_journal.presentation.framework import (
    _advance_review_queue,
    _auto_risk_label,
    _automatic_risk_monitoring_detail,
    _clear_review_dialog,
    _default_policy_adherence_grade,
    _daily_r_metric,
    _drawdown_metric,
    _focus_metric_text,
    _process_failure_detail,
    _readiness_metric,
    _risk_evidence_detail,
    _risk_state_metric,
    _set_pillar_grades_to_pass,
)
from trading_journal.presentation.trade_tags import direction_tag, outcome_tag


ALL_PASS = {criterion: "pass" for criterion in ASSESSMENT_CRITERIA}


def test_monitor_metrics_use_semantic_colors_without_treating_status_as_a_trend() -> None:
    incomplete = ReadinessAssessment(None, "incomplete", 20, "More evidence required.")
    ready = ReadinessAssessment("82", "ready", 20, "Ready.")
    clear = RiskSnapshot(True, "clear", "-1.05", "-1.05", "2.7", "2.7", 1, "Clear.")

    assert _readiness_metric(incomplete) == (None, "Incomplete", "orange")
    assert _readiness_metric(ready) == ("82%", "Ready", "green")
    assert _risk_state_metric(clear) == ("Clear", "Within limits", "green")
    assert _daily_r_metric(clear.daily_r) == ("−1.05R", "Loss", "red")
    assert _drawdown_metric(clear.max_drawdown_percent) == ("2.7%", "Historical maximum", "gray")


def test_monitor_metrics_distinguish_unavailable_values_and_breached_drawdown() -> None:
    assert _daily_r_metric(None) == (None, "Unavailable", "gray")
    assert _drawdown_metric(None) == (None, "Unavailable", "gray")
    assert _drawdown_metric("0") == ("0.0%", "No drawdown", "gray")


def test_coaching_focus_metrics_use_compact_display_formatting() -> None:
    assert _focus_metric_text("77.777777777", "component") == "78%"
    assert _focus_metric_text("65", "criterion") == "65%"
    assert _focus_metric_text("9", "manual_evidence") == "9"
    assert _focus_metric_text(None, "violation") == "—"


def test_trade_tags_keep_direction_and_realized_outcome_separate() -> None:
    assert direction_tag("long").label == "Long"
    assert direction_tag("short").label == "Short"
    assert outcome_tag("20").label == "Profit"
    assert outcome_tag("-20").label == "Loss"
    assert outcome_tag("0").label == "Breakeven"


def test_marking_a_pillar_as_pass_changes_only_its_criteria() -> None:
    state = {"assessment-42-rule_adherence": "Fail", "assessment-42-policy_adherence": "Partial"}

    _set_pillar_grades_to_pass(42, ("rule_adherence", "impulse_control"), state)

    assert state == {
        "assessment-42-rule_adherence": "Pass",
        "assessment-42-impulse_control": "Pass",
        "assessment-42-policy_adherence": "Partial",
    }


def test_marking_all_criteria_as_pass_sets_every_criterion() -> None:
    state = {"assessment-42-rule_adherence": "Fail", "assessment-42-policy_adherence": "Partial"}

    _set_pillar_grades_to_pass(42, ASSESSMENT_CRITERIA, state)

    assert state == {f"assessment-42-{criterion}": "Pass" for criterion in ASSESSMENT_CRITERIA}


def test_default_policy_adherence_grade_matches_within_policy_state() -> None:
    assert _default_policy_adherence_grade("within_policy") == "pass"


def test_default_policy_adherence_grade_matches_over_policy_state() -> None:
    assert _default_policy_adherence_grade("over_policy") == "fail"


def test_default_policy_adherence_grade_is_blank_when_unavailable() -> None:
    assert _default_policy_adherence_grade("unavailable") is None


def test_advance_review_queue_returns_next_id_and_remainder() -> None:
    assert _advance_review_queue((7, 9, 11)) == (7, (9, 11))


def test_advance_review_queue_on_empty_queue_returns_none() -> None:
    assert _advance_review_queue(()) == (None, ())


def test_clear_review_dialog_also_clears_the_queue() -> None:
    st.session_state["post-trade-review-trade-id"] = 42
    st.session_state["post-trade-review-queue"] = (7, 9)

    _clear_review_dialog()

    assert "post-trade-review-trade-id" not in st.session_state
    assert "post-trade-review-queue" not in st.session_state


def _repository(tmp_path):
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD", export_file_path="", opening_balance="1000"
    )
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    return repository, account.id


def _policy(
    repository: SQLiteJournalRepository,
    account_id: int,
    *,
    pretrade_balance_auto_evidence_enabled: bool = False,
    daily_loss_limit_r: str = "2",
    weekly_loss_limit_r: str = "4",
    max_drawdown_percent: str = "10",
):
    return repository.save_account_risk_policy(
        account_id=account_id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r=daily_loss_limit_r,
        weekly_loss_limit_r=weekly_loss_limit_r,
        max_drawdown_percent=max_drawdown_percent,
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        pretrade_balance_auto_evidence_enabled=pretrade_balance_auto_evidence_enabled,
    )


def _strategy(repository: SQLiteJournalRepository):
    profile = repository.save_strategy_profile(
        name="Trend continuation",
        description="Trade a confirmed pullback continuation.",
        backtest_verified=True,
        backtest_notes="Representative sample including modeled costs.",
    )
    repository.save_strategy_setup(
        strategy_profile_id=profile.id,
        name="Standard pullback",
        description="Valid: pullback holds prior structure. Invalid: break of structure before entry.",
    )
    return profile


def _import_position(
    repository: SQLiteJournalRepository,
    account_id: int,
    *,
    position_id: str = "1001",
    net_pnl: str = "20",
    entry_time: str = "2026-08-10T08:00:00+00:00",
    exit_time: str = "2026-08-10T09:00:00+00:00",
    initial_risk_amount: str | None = None,
    initial_reward_amount: str | None = None,
    entry_stop_price: str | None = None,
    close_stop_price: str | None = None,
    account_balance: str | None = None,
    pretrade_account_balance: str | None = None,
) -> int:
    repository.upsert_mt5_positions(
        account_id,
        [
            MT5PositionExport(
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id=position_id,
                symbol="XAUUSD",
                direction="long",
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl=net_pnl,
                commission="0",
                swap="0",
                fees="0",
                net_pnl=net_pnl,
                initial_risk_amount=initial_risk_amount,
                initial_reward_amount=initial_reward_amount,
                entry_stop_price=entry_stop_price,
                close_stop_price=close_stop_price,
                entry_deal_count=1,
                account_balance=account_balance,
                pretrade_account_balance=pretrade_account_balance,
            )
        ],
        "positions.csv",
        f"hash-{position_id}",
        live_account_balance=Decimal(account_balance) if account_balance else None,
    )
    return next(item.id for item in repository.list_closed_trades_for_review(account_id) if item.position_id == position_id)


def _review(
    repository: SQLiteJournalRepository,
    account_id: int,
    trade_id: int,
    policy,
    strategy,
    *,
    grades: dict[str, str] | None = None,
    tags: tuple[str, ...] = (),
    hard_rules: tuple[str, ...] = (),
    actual_risk: str | None = "10",
    action: str | None = None,
):
    return repository.save_post_trade_assessment(
        account_id=account_id,
        trade_id=trade_id,
        risk_policy_id=policy.id,
        strategy_profile_id=strategy.id,
        criterion_grades=ALL_PASS if grades is None else grades,
        violation_codes=tags,
        hard_rule_codes=hard_rules,
        declared_actual_risk_amount=actual_risk,
        post_review_note="Reviewed independently of trade P&L.",
        corrective_action=action,
    )


def test_full_assessment_requires_every_explicit_grade(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)

    with pytest.raises(ValueError, match="Every three-pillar criterion"):
        _review(repository, account_id, trade_id, policy, strategy, grades={"rule_adherence": "pass"})


def test_failed_criterion_requires_reason_and_corrective_action(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    grades = {**ALL_PASS, "entry_fidelity": "fail"}

    with pytest.raises(ValueError, match="reason tag"):
        _review(repository, account_id, trade_id, policy, strategy, grades=grades)
    with pytest.raises(ValueError, match="corrective action"):
        _review(repository, account_id, trade_id, policy, strategy, grades=grades, tags=("fomo_or_chase",))

    assessment = _review(repository, account_id, trade_id, policy, strategy, grades=grades, tags=("fomo_or_chase",), action="Wait for the entry trigger.")
    assert assessment.criterion_grades["entry_fidelity"] == "fail"


def test_trade_score_uses_documented_weights_and_keeps_raw_scores(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    grades = {**ALL_PASS, "rule_adherence": "partial"}
    _review(repository, account_id, trade_id, policy, strategy, grades=grades, tags=("fomo_or_chase",), action="Use the written rules.")

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.psychology_score == "82.5"
    assert score.risk_score == score.system_score == "100"
    assert score.overall_score == "94.16666666666666666666666667"
    assert score.process_status == "PASS"
    assert score.classification == "Good Win"


def test_hard_rule_fails_process_even_for_a_profitable_trade(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, net_pnl="40")
    grades = {**ALL_PASS, "setup_validity": "fail"}
    _review(
        repository, account_id, trade_id, policy, strategy, grades=grades,
        tags=("mandatory_setup_absent",), hard_rules=("mandatory_setup_absent",), action="Do not take this setup again.",
    )

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.system_score == "70"
    assert score.overall_score is not None
    assert score.process_status == "FAIL"
    assert score.system_hard_block
    assert score.classification == "Bad Win"


def test_hard_rule_status_is_snapshotted_when_framework_rules_later_change(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    reviewed_trade_id = _import_position(repository, account_id, position_id="reviewed-hard-rule")
    _review(
        repository,
        account_id,
        reviewed_trade_id,
        policy,
        strategy,
        hard_rules=("stop_widened",),
        action="Keep the stop at the documented invalidation point.",
    )
    repository.save_framework_rule_settings(
        oversized_revenge_hard=True,
        mandatory_setup_hard=True,
        stop_widened_hard=False,
        shutdown_breach_hard=True,
        repeated_critical_threshold=2,
    )

    reviewed_score = next(
        item for item in FrameworkService(repository).trade_process_scores(account_id)
        if item.trade_id == reviewed_trade_id
    )

    assert reviewed_score.process_status == "FAIL"
    assert reviewed_score.classification == "Bad Win"

    new_trade_id = _import_position(repository, account_id, position_id="disabled-hard-rule")
    with pytest.raises(ValueError, match="Enable a hard-rule event"):
        _review(
            repository,
            account_id,
            new_trade_id,
            policy,
            strategy,
            hard_rules=("stop_widened",),
            action="Keep the stop at the documented invalidation point.",
        )


def test_low_raw_score_is_not_classified_as_a_good_trade_without_a_hard_rule(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, net_pnl="40")
    _review(
        repository,
        account_id,
        trade_id,
        policy,
        strategy,
        grades={criterion: "fail" for criterion in ALL_PASS},
        tags=("fomo_or_chase",),
        action="Trade only a documented setup.",
    )

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.overall_score == "0"
    assert score.process_status == "PASS"
    assert score.quality_status == "needs_improvement"
    assert score.classification == "Needs improvement Win"


def test_within_policy_automatic_risk_is_reviewed_evidence_after_approval(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    trade_id = _import_position(repository, account_id, initial_risk_amount="8", initial_reward_amount="16", entry_stop_price="3290")

    service = FrameworkService(repository)
    pending = next(item for item in service.trade_process_scores(account_id) if item.trade_id == trade_id)
    pending_pillars = service.pillar_scores(account_id)

    assert pending.assessment_state == "not_scored"
    assert pending.review_kind == "auto_review"
    assert pending.auto_risk.state == "within_policy"
    assert pending.psychology_score is None
    assert all(item.reviewed_total == 0 for item in pending_pillars)

    repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=pending.risk_evidence_source, risk_policy_state=pending.risk_policy_state,
        actual_risk_amount=pending.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(pending.risk_policy_state),
    )
    after_approval = FrameworkService(repository)
    approved = next(item for item in after_approval.trade_process_scores(account_id) if item.trade_id == trade_id)
    approved_pillars = after_approval.pillar_scores(account_id)

    assert approved.assessment_state == "reviewed"
    assert approved.review_kind == "approved_auto_review"
    assert approved.psychology_score == "50"
    assert approved.risk_score == "67.5"
    assert approved.system_score == "50"
    assert all(item.reviewed_total == 1 for item in approved_pillars)
    coverage = after_approval.risk_evidence_coverage(account_id)
    assert coverage.total == 1
    assert coverage.approved == 1
    assert coverage.pending == 0
    assert len(after_approval.rolling_score_trend(account_id)) == 1
    assert after_approval.period_review_status(
        account_id, "weekly", now=datetime(2026, 8, 17, tzinfo=timezone.utc)
    ).reviewed_trades == 1


def test_risk_snapshot_clears_an_expired_daily_limit_breach(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id, position_id="breach-1", net_pnl="-20", exit_time="2026-08-01T09:00:00+00:00")
    _import_position(repository, account_id, position_id="breach-2", net_pnl="-20", exit_time="2026-08-01T10:00:00+00:00")
    service = FrameworkService(repository)

    breached_day = service.risk_snapshot(account_id, now=datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
    later_day = service.risk_snapshot(account_id, now=datetime(2026, 8, 3, 12, tzinfo=timezone.utc))

    assert breached_day.state == "stop"
    assert later_day.state == "clear"


def test_risk_snapshot_accumulates_pnl_against_policy_standard_risk(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(
        repository,
        account_id,
        position_id="large-actual-risk-loss",
        net_pnl="-20",
        initial_risk_amount="40",
        entry_stop_price="3290",
    )
    _import_position(
        repository,
        account_id,
        position_id="small-actual-risk-win",
        net_pnl="10",
        initial_risk_amount="5",
        entry_stop_price="3290",
    )

    snapshot = FrameworkService(repository).risk_snapshot(
        account_id,
        now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
    )

    # Funded capital is $1,000 and standard risk is 1%, so the day's
    # combined -$10 is -1R regardless of each trade's actual risk amount.
    assert snapshot.daily_r == "-1"


def test_automatic_risk_limit_is_advisory_and_flags_a_later_shutdown_candidate(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    _import_position(
        repository,
        account_id,
        position_id="loss-1",
        net_pnl="-10",
        entry_time="2026-08-10T08:00:00+00:00",
        exit_time="2026-08-10T09:00:00+00:00",
    )
    limit_trade_id = _import_position(
        repository,
        account_id,
        position_id="loss-2",
        net_pnl="-10",
        entry_time="2026-08-10T09:05:00+00:00",
        exit_time="2026-08-10T10:00:00+00:00",
    )
    reviewed_trade_id = _import_position(
        repository,
        account_id,
        position_id="after-limit",
        net_pnl="5",
        entry_time="2026-08-10T10:05:00+00:00",
        exit_time="2026-08-10T11:00:00+00:00",
    )
    _review(repository, account_id, limit_trade_id, policy, strategy)
    _review(repository, account_id, reviewed_trade_id, policy, strategy)

    scores = {item.trade_id: item for item in FrameworkService(repository).trade_process_scores(account_id)}
    limit_score = scores[limit_trade_id]
    score = scores[reviewed_trade_id]

    assert limit_score.process_status == "PASS"
    assert limit_score.automatic_risk_event_codes == ("daily_limit",)
    assert score.process_status == "PASS"
    assert score.shutdown_candidate_codes == ("daily_limit",)
    assert _process_failure_detail(score) is None
    monitoring_detail = _automatic_risk_monitoring_detail(score)
    assert monitoring_detail is not None
    assert "Shutdown review" in monitoring_detail
    assert "Daily loss limit" in monitoring_detail


def test_confirmed_shutdown_breach_is_a_hard_process_failure(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    _review(
        repository,
        account_id,
        trade_id,
        policy,
        strategy,
        hard_rules=("shutdown_breach",),
        action="Stop trading after the configured shutdown rule is reached.",
    )

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.process_status == "FAIL"
    assert score.risk_hard_block
    assert "Traded after hard shutdown" in (_process_failure_detail(score) or "")


def test_real_loss_and_disabled_pretrade_balance_leave_no_profitable_no_sl_evidence(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    loser = _import_position(repository, account_id, position_id="loss", net_pnl="-8")
    winner = _import_position(repository, account_id, position_id="win", net_pnl="8", pretrade_account_balance="1000")
    scores = {item.trade_id: item for item in FrameworkService(repository).trade_process_scores(account_id)}

    assert scores[loser].auto_risk.risk_basis == "real_loss_sl"
    assert scores[loser].auto_risk.confidence == "inferred"
    assert scores[winner].auto_risk.risk_basis == "unavailable"


def test_enabled_pretrade_balance_is_advisory_no_sl_risk_evidence(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id, pretrade_balance_auto_evidence_enabled=True)
    winner = _import_position(repository, account_id, position_id="win", net_pnl="8", pretrade_account_balance="900")

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == winner)

    assert score.assessment_state == "not_scored"
    assert score.review_kind == "needs_approval"
    assert score.auto_risk.risk_basis == "pretrade_account_balance_sl"
    assert score.auto_risk.source_amount == "900"
    assert score.auto_risk.confidence == "conservative"


def test_approval_needed_auto_review_can_be_approved_and_then_replaced_by_full_review(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, initial_risk_amount="20", entry_stop_price="3290")
    before = FrameworkService(repository).trade_process_scores(account_id)[0]
    assert before.review_kind == "needs_approval"
    approval = repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )
    assert approval.risk_policy_state == "over_policy"
    approved = FrameworkService(repository).trade_process_scores(account_id)[0]
    assert approved.review_kind == "approved_auto_review"
    assert approved.risk_score == "32.5"
    _review(repository, account_id, trade_id, policy, strategy)
    after_manual = FrameworkService(repository)
    assert after_manual.trade_process_scores(account_id)[0].review_kind == "manual_review"
    assert all(item.reviewed_total == 1 for item in after_manual.pillar_scores(account_id))


def test_upgrading_an_auto_review_to_manual_archives_it_as_a_revision(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, initial_risk_amount="20", entry_stop_price="3290")
    before = FrameworkService(repository).trade_process_scores(account_id)[0]
    repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )

    upgraded = _review(repository, account_id, trade_id, policy, strategy, actual_risk=None)

    assert upgraded.version == 2
    revisions = repository.list_post_trade_assessment_revisions(trade_id)
    assert len(revisions) == 1
    assert revisions[0].version == 1
    assert revisions[0].method == "auto"


def test_upgrading_an_auto_review_requires_hard_rules_to_be_currently_enabled(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, initial_risk_amount="20", entry_stop_price="3290")
    before = FrameworkService(repository).trade_process_scores(account_id)[0]
    repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )
    repository.save_framework_rule_settings(
        oversized_revenge_hard=True,
        mandatory_setup_hard=False,
        stop_widened_hard=True,
        shutdown_breach_hard=True,
        repeated_critical_threshold=2,
    )

    with pytest.raises(ValueError, match="Enable a hard-rule event"):
        _review(
            repository, account_id, trade_id, policy, strategy,
            hard_rules=("mandatory_setup_absent",), actual_risk=None,
            action="Confirm the setup criteria before entry.",
        )


def test_upgrading_an_auto_review_does_not_inherit_reviewed_actual_risk(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, initial_risk_amount="20", entry_stop_price="3290")
    before = FrameworkService(repository).trade_process_scores(account_id)[0]
    repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )

    _review(repository, account_id, trade_id, policy, strategy, actual_risk=None)

    after = FrameworkService(repository).trade_process_scores(account_id)[0]
    assert after.risk_evidence_source == "specific_preset_sl"
    assert after.risk_evidence_source != "reviewed_actual_risk"


def test_approve_auto_review_is_idempotent(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    trade_id = _import_position(repository, account_id, initial_risk_amount="20", entry_stop_price="3290")
    before = FrameworkService(repository).trade_process_scores(account_id)[0]

    first = repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )
    second = repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )

    assert first.id == second.id
    assert len(repository.list_active_post_trade_assessments(account_id)) == 1


def test_reviewed_actual_risk_replaces_automatic_policy_comparison_only(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, net_pnl="-20")
    _review(repository, account_id, trade_id, policy, strategy, actual_risk="5")

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.auto_risk.state == "over_policy"
    assert score.actual_risk_amount == "5"
    assert score.risk_evidence_source == "reviewed_actual_risk"
    assert score.risk_policy_state == "within_policy"
    assert _auto_risk_label(score) == "Reviewed actual risk · Within policy"
    assert "immutable MT5 positions" in _risk_evidence_detail(score)


def test_drawdown_shutdown_candidate_clears_after_balance_recovers(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id, daily_loss_limit_r="20", weekly_loss_limit_r="40")
    _import_position(
        repository,
        account_id,
        position_id="drawdown-loss",
        net_pnl="-100",
        entry_time="2026-08-10T08:00:00+00:00",
        exit_time="2026-08-10T09:00:00+00:00",
    )
    _import_position(
        repository,
        account_id,
        position_id="drawdown-recovery",
        net_pnl="100",
        entry_time="2026-08-11T08:00:00+00:00",
        exit_time="2026-08-11T09:00:00+00:00",
    )
    later_trade_id = _import_position(
        repository,
        account_id,
        position_id="after-recovery",
        net_pnl="5",
        entry_time="2026-08-12T08:00:00+00:00",
        exit_time="2026-08-12T09:00:00+00:00",
    )

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == later_trade_id)

    assert score.shutdown_candidate_codes == ()


def test_grouped_positions_become_one_logical_trade_for_review_and_dashboard(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    first = _import_position(repository, account_id, position_id="scale-1", net_pnl="12", exit_time="2026-08-10T09:00:00+00:00")
    second = _import_position(repository, account_id, position_id="scale-2", net_pnl="8", exit_time="2026-08-10T10:00:00+00:00")

    group_id = repository.create_logical_trade_group(
        account_id=account_id,
        logical_trade_ids=(first, second),
        display_label="London scale-in",
    )
    grouped = repository.list_closed_trades_for_review(account_id)

    assert len(grouped) == 1
    assert grouped[0].id == group_id
    assert grouped[0].display_label == "London scale-in"
    assert grouped[0].position_ids == ("scale-1", "scale-2")
    assert grouped[0].position_count == 2
    assert grouped[0].net_pnl == "20"
    assert grouped[0].exit_time == "2026-08-10T10:00:00+00:00"

    _review(repository, account_id, group_id, policy, strategy)
    scores = FrameworkService(repository).trade_process_scores(account_id)
    report = DashboardService(repository).build_report(account_id=account_id, start_date="2026-08-10", end_date="2026-08-10")

    assert len(scores) == 1
    assert scores[0].assessment_state == "reviewed"
    assert report.trade_count == 1
    assert report.net_pnl == "20"
    assert report.per_trade[0].position_count == 2
    assert report.per_trade[0].logical_trade_id == group_id
    assert report.per_trade[0].display_label == "London scale-in"
    trade_concentration = next(item for item in report.concentration if item.dimension == "trade")
    assert [(item.label, item.trade_count, item.amount) for item in trade_concentration.profit.items] == [
        (f"LT-{group_id} · London scale-in", 1, "20"),
    ]


def test_disbanding_a_reviewed_group_supersedes_its_assessment_and_restores_singletons(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    first = _import_position(repository, account_id, position_id="draft-1")
    second = _import_position(repository, account_id, position_id="draft-2")
    group_id = repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label=None)

    repository.disband_logical_trade_group(account_id=account_id, logical_trade_id=group_id)
    singletons = repository.list_closed_trades_for_review(account_id)
    assert len(singletons) == 2
    group_id = repository.create_logical_trade_group(
        account_id=account_id,
        logical_trade_ids=tuple(item.id for item in singletons),
        display_label=None,
    )
    _review(repository, account_id, group_id, policy, strategy)

    result = repository.disband_logical_trade_group(account_id=account_id, logical_trade_id=group_id)

    assert result.superseded_assessment_count == 1
    assert repository.get_post_trade_assessment_for_trade(group_id) is None
    assert len(repository.list_closed_trades_for_review(account_id)) == 2
    assert len(repository.list_post_trade_assessment_outcomes(account_id)) == 0


def test_regrouping_reviewed_trades_supersedes_scores_and_keeps_member_audit(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    first = _import_position(repository, account_id, position_id="reviewed-1", net_pnl="12")
    second = _import_position(repository, account_id, position_id="reviewed-2", net_pnl="8")
    third = _import_position(repository, account_id, position_id="reviewed-3", net_pnl="4")
    group_id = repository.create_logical_trade_group(
        account_id=account_id,
        logical_trade_ids=(first, second),
        display_label="Initial scale-in",
    )
    _review(repository, account_id, group_id, policy, strategy)
    current = {item.id: item for item in repository.list_closed_trades_for_review(account_id)}
    preview = repository.preview_logical_trade_regroup(
        account_id=account_id,
        logical_trade_id=group_id,
        position_trade_ids=(current[group_id].members[0].id, current[third].members[0].id),
    )

    result = repository.regroup_logical_trade(
        account_id=account_id,
        logical_trade_id=group_id,
        position_trade_ids=(current[group_id].members[0].id, current[third].members[0].id),
        display_label="Corrected scale-in",
    )
    active = {item.id: item for item in repository.list_closed_trades_for_review(account_id)}
    archived = repository.list_superseded_post_trade_assessments_for_trade(
        account_id=account_id,
        logical_trade_id=group_id,
    )
    report = DashboardService(repository).build_report(
        account_id=account_id,
        start_date="2026-08-10",
        end_date="2026-08-10",
    )

    assert preview.affected_assessment_count == 1
    assert result.superseded_assessment_count == 1
    assert active[group_id].position_ids == ("reviewed-1", "reviewed-3")
    assert active[group_id].display_label == "Corrected scale-in"
    assert any(item.position_id == "reviewed-2" for item in active.values())
    assert repository.get_post_trade_assessment_for_trade(group_id) is None
    assert archived[0].assessed_position_ids == ("reviewed-1", "reviewed-2")
    assert archived[0].assessed_trade_label == "Initial scale-in"
    assert archived[0].superseded_reason == "Logical-trade membership changed"
    assert len(repository.list_post_trade_assessment_outcomes(account_id)) == 0
    assert all(item.strategy == "Trend continuation" for item in repository.list_trade_performance(account_id))
    assert report.trade_count == 2
    assert report.net_pnl == "24"

    replacement = _review(repository, account_id, group_id, policy, strategy)

    assert replacement.assessed_position_ids == ("reviewed-1", "reviewed-3")
    assert len(repository.list_post_trade_assessment_outcomes(account_id)) == 1
    assert len(repository.list_superseded_post_trade_assessments_for_trade(
        account_id=account_id,
        logical_trade_id=group_id,
    )) == 1


def test_relabeling_a_reviewed_logical_trade_keeps_its_active_assessment(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    first = _import_position(repository, account_id, position_id="label-1")
    second = _import_position(repository, account_id, position_id="label-2")
    group_id = repository.create_logical_trade_group(
        account_id=account_id,
        logical_trade_ids=(first, second),
        display_label="Before rename",
    )
    _review(repository, account_id, group_id, policy, strategy)
    group = next(item for item in repository.list_closed_trades_for_review(account_id) if item.id == group_id)

    result = repository.regroup_logical_trade(
        account_id=account_id,
        logical_trade_id=group_id,
        position_trade_ids=tuple(member.id for member in group.members),
        display_label="After rename",
    )

    assert result.superseded_assessment_count == 0
    assert repository.get_post_trade_assessment_for_trade(group_id) is not None
    assert next(item for item in repository.list_closed_trades_for_review(account_id) if item.id == group_id).display_label == "After rename"


def test_draft_group_can_swap_a_member_without_rewriting_mt5_positions(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    first = _import_position(repository, account_id, position_id="edit-1")
    second = _import_position(repository, account_id, position_id="edit-2")
    third = _import_position(repository, account_id, position_id="edit-3")
    group_id = repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label="First attempt")
    group = next(item for item in repository.list_closed_trades_for_review(account_id) if item.id == group_id)
    third_item = next(item for item in repository.list_closed_trades_for_review(account_id) if item.id == third)

    repository.update_logical_trade_group(
        account_id=account_id,
        logical_trade_id=group_id,
        position_trade_ids=(group.members[0].id, third_item.members[0].id),
        display_label="Correct scale-in",
    )

    logical_trades = repository.list_closed_trades_for_review(account_id)
    edited = next(item for item in logical_trades if item.id == group_id)
    restored = next(item for item in logical_trades if item.position_id == "edit-2")
    assert edited.position_ids == ("edit-1", "edit-3")
    assert edited.display_label == "Correct scale-in"
    assert restored.position_count == 1


def test_group_rejects_positions_with_different_imported_risk_policy_versions(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    first = _import_position(repository, account_id, position_id="policy-1")
    _policy(repository, account_id)
    second = _import_position(repository, account_id, position_id="policy-2")

    with pytest.raises(ValueError, match="Risk-policy version"):
        repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label=None)


def test_group_pretrade_balance_risk_is_one_account_level_fallback(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id, pretrade_balance_auto_evidence_enabled=True)
    winning = _import_position(repository, account_id, position_id="win", net_pnl="8", entry_time="2026-08-10T07:00:00+00:00", pretrade_account_balance="1000")
    losing = _import_position(repository, account_id, position_id="loss", net_pnl="-8")
    group_id = repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(losing, winning), display_label=None)

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.trade_id == group_id
    assert score.auto_risk.risk_basis == "mixed_sources"
    assert score.auto_risk.confidence == "conservative"
    assert score.auto_risk.source_amount == "1000"
    assert score.auto_risk.state == "over_policy"
    assert _auto_risk_label(score) == "Mixed estimates · Over policy"
    assert "applied once for the logical trade" in score.auto_risk.detail


def test_grouped_winners_do_not_multiply_the_pretrade_balance_fallback(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id, pretrade_balance_auto_evidence_enabled=True)
    first = _import_position(repository, account_id, position_id="win-1", net_pnl="8", pretrade_account_balance="1000")
    second = _import_position(repository, account_id, position_id="win-2", net_pnl="6", pretrade_account_balance="1000")
    repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label=None)

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.auto_risk.risk_basis == "pretrade_account_balance_sl"
    assert score.auto_risk.source_amount == "1000"
    assert score.auto_risk.state == "over_policy"


def test_logical_grouping_does_not_rewrite_account_balance_history(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    first = _import_position(repository, account_id, position_id="cash-1", net_pnl="12", exit_time="2026-08-09T09:00:00+00:00")
    second = _import_position(repository, account_id, position_id="cash-2", net_pnl="8", exit_time="2026-08-10T09:00:00+00:00")
    repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label="Two-day scale-out")

    early = DashboardService(repository).build_report(account_id=account_id, start_date="2026-08-09", end_date="2026-08-09")
    full = DashboardService(repository).build_report(account_id=account_id, start_date="2026-08-09", end_date="2026-08-10")

    assert early.trade_count == 0
    assert early.raw_position_count == 1
    assert early.net_pnl == "12"
    assert early.ending_balance == "1012"
    assert [(item.date, item.net_pnl) for item in early.daily] == [("2026-08-09", "12")]
    assert full.trade_count == 1
    assert full.raw_position_count == 2
    assert full.ending_balance == "1020"


def test_account_drawdown_uses_raw_position_chronology_within_a_day(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    first = _import_position(repository, account_id, position_id="drawdown-1", net_pnl="20", exit_time="2026-08-10T09:00:00+00:00")
    second = _import_position(repository, account_id, position_id="drawdown-2", net_pnl="-5", exit_time="2026-08-10T10:00:00+00:00")
    repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label="Same-day scale-out")

    report = DashboardService(repository).build_report(account_id=account_id, start_date="2026-08-10", end_date="2026-08-10")

    assert report.max_drawdown == "5"
    assert report.current_drawdown == "5"
    assert report.daily[0].net_pnl == "15"


def test_framework_service_reuses_scores_within_one_render_context(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id)
    service = FrameworkService(repository)
    calls = 0
    original = repository.list_closed_trades_for_review

    def counted(account: int):
        nonlocal calls
        calls += 1
        return original(account)

    repository.list_closed_trades_for_review = counted  # type: ignore[method-assign]

    service.trade_process_scores(account_id)
    service.risk_snapshot(account_id)
    service.trade_process_scores(account_id)

    assert calls == 1


def test_group_with_missing_member_risk_evidence_keeps_partial_total_advisory(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    known = _import_position(repository, account_id, position_id="known", initial_risk_amount="8")
    unknown = _import_position(repository, account_id, position_id="unknown", net_pnl="8")
    repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(known, unknown), display_label=None)

    score = FrameworkService(repository).trade_process_scores(account_id)[0]

    assert score.auto_risk.source_amount == "8"
    assert score.auto_risk.state == "unavailable"
    assert "policy compliance is unavailable" in score.auto_risk.detail


def test_grouping_does_not_hide_raw_position_risk_limits(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    first = _import_position(repository, account_id, position_id="risk-1", net_pnl="-20", exit_time="2026-08-10T09:00:00+00:00")
    second = _import_position(repository, account_id, position_id="risk-2", net_pnl="-20", exit_time="2026-08-10T10:00:00+00:00")
    repository.create_logical_trade_group(account_id=account_id, logical_trade_ids=(first, second), display_label=None)

    snapshot = FrameworkService(repository).risk_snapshot(account_id, now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc))

    assert snapshot.daily_r == "-4"
    assert snapshot.state == "stop"


def test_a_missing_component_is_excluded_and_the_rest_renormalized(tmp_path, monkeypatch) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    _review(repository, account_id, trade_id, policy, strategy)

    original_components = FrameworkService._period_components

    def fake_components(self, pillar, sample, historical_events, pnl_by_trade):
        if pillar != "risk":
            return original_components(self, pillar, sample, historical_events, pnl_by_trade)
        return (
            ("Policy adherence", Decimal("100")),
            ("Stop discipline", None),
            ("Limit compliance", Decimal("80")),
            ("Exposure control", Decimal("60")),
        )

    monkeypatch.setattr(FrameworkService, "_period_components", fake_components)

    risk = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}["risk"]

    # weights for risk are (0.35, 0.25, 0.25, 0.15); excluding the None (weight 0.25)
    # and renormalizing over the remaining 0.75: (100*0.35 + 80*0.25 + 60*0.15) / 0.75
    expected = (Decimal("100") * Decimal("0.35") + Decimal("80") * Decimal("0.25") + Decimal("60") * Decimal("0.15")) / Decimal("0.75")
    assert Decimal(risk.raw_score) == expected
    assert risk.score is not None


def test_period_scores_use_the_documented_components(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(20):
        trade_id = _import_position(repository, account_id, position_id=f"trade-{index}", exit_time=f"2026-08-{index + 1:02d}T09:00:00+00:00")
        _review(repository, account_id, trade_id, policy, strategy)

    scores = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}

    assert all(item.score == "100" for item in scores.values())
    assert scores["psychology"].component_scores[-1] == ("Post-loss discipline", "100")
    assert scores["system"].component_scores[-1] == ("Edge evidence", "100")


def test_editing_a_strategy_later_does_not_change_an_already_reviewed_trades_score(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(20):
        trade_id = _import_position(repository, account_id, position_id=f"trade-{index}", exit_time=f"2026-08-{index + 1:02d}T09:00:00+00:00")
        _review(repository, account_id, trade_id, policy, strategy)

    repository.save_strategy_profile(
        strategy_id=strategy.id,
        name=strategy.name,
        description=None,
        backtest_verified=False,
        backtest_notes=None,
    )

    scores = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}

    assert scores["system"].component_scores[-1] == ("Edge evidence", "100")


def test_repeated_critical_breaches_cap_numeric_score_and_fail_pillar(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(2):
        trade_id = _import_position(repository, account_id, position_id=f"trade-{index}")
        _review(repository, account_id, trade_id, policy, strategy, tags=("stop_widened",), hard_rules=("stop_widened",), action="Do not widen stops.")

    risk = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}["risk"]

    assert risk.hard_block
    assert risk.score == "59"
    assert risk.status == "fail"


def test_weekly_period_review_captures_scores_and_resolves_repeated_cap(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(2):
        trade_id = _import_position(repository, account_id, position_id=f"trade-{index}", exit_time=f"2026-08-0{index + 3}T09:00:00+00:00")
        _review(repository, account_id, trade_id, policy, strategy, tags=("stop_widened",), hard_rules=("stop_widened",), action="Do not widen stops.")

    service = FrameworkService(repository)
    status = service.period_review_status(account_id, "weekly", now=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert status.due
    assert status.closed_trades == 2
    service.save_period_review(
        account_id=account_id, cadence="weekly", review_note="Stops widened twice.", priority_action="Use fixed stop orders.", now=datetime(2026, 8, 10, tzinfo=timezone.utc)
    )
    saved = repository.list_framework_period_reviews(account_id)

    assert len(saved) == 1
    assert saved[0].cadence == "weekly"
    assert saved[0].priority_action == "Use fixed stop orders."


def test_period_review_status_reports_no_activity_when_nothing_closed(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)

    status = FrameworkService(repository).period_review_status(account_id, "weekly", now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert status.closed_trades == 0
    assert status.reviewed_trades == 0
    assert not status.due


def test_period_review_status_distinguishes_pending_from_no_activity(tmp_path) -> None:
    """A closed-but-unreviewed trade must not read the same as no activity at all - the
    caller (the widget) needs closed_trades to tell these apart, since due/reviewed_trades
    alone can't (see docs/period-review-status-investigation.md).
    """
    repository, account_id = _repository(tmp_path)
    _import_position(repository, account_id, exit_time="2026-08-05T09:00:00+00:00")

    status = FrameworkService(repository).period_review_status(account_id, "weekly", now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert status.closed_trades == 1
    assert status.reviewed_trades == 0
    assert not status.due


def test_caution_cap_detail_names_the_date_of_the_last_critical_violation(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(2):
        trade_id = _import_position(repository, account_id, position_id=f"trade-{index}", exit_time=f"2026-08-0{index + 3}T09:00:00+00:00")
        _review(repository, account_id, trade_id, policy, strategy, tags=("revenge",), action="Step away after a loss before re-entering.")

    psychology = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}["psychology"]

    assert psychology.status == "caution"
    assert not psychology.hard_block
    assert "2026-08-04" in psychology.detail


def test_readiness_is_the_lowest_complete_pillar_and_requires_window(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(20):
        trade_id = _import_position(repository, account_id, position_id=f"trade-{index}")
        grades = {**ALL_PASS, "context_alignment": "partial"} if index == 0 else ALL_PASS
        action = "Review context." if index == 0 else None
        tags = ("fomo_or_chase",) if index == 0 else ()
        _review(repository, account_id, trade_id, policy, strategy, grades=grades, tags=tags, action=action)

    readiness = FrameworkService(repository).readiness(account_id)

    assert readiness.status == "ready"
    assert Decimal(readiness.score) < Decimal("100")


def test_readiness_remains_incomplete_until_the_selected_sample_is_full(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    _review(repository, account_id, trade_id, policy, strategy)

    readiness = FrameworkService(repository).readiness(account_id, window=20)

    assert readiness.score is None
    assert readiness.status == "incomplete"


def test_period_review_snapshots_the_completed_period_not_later_trades(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for day in (3, 4):
        trade_id = _import_position(repository, account_id, position_id=f"period-{day}", exit_time=f"2026-08-{day:02d}T09:00:00+00:00")
        _review(repository, account_id, trade_id, policy, strategy)
    later_trade = _import_position(repository, account_id, position_id="later", exit_time="2026-08-10T09:00:00+00:00")
    grades = {**ALL_PASS, "rule_adherence": "partial"}
    _review(repository, account_id, later_trade, policy, strategy, grades=grades, tags=("fomo_or_chase",), action="Wait for the written setup.")

    FrameworkService(repository).save_period_review(
        account_id=account_id,
        cadence="weekly",
        review_note="The prior week was clean.",
        priority_action="Keep the same process.",
        now=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    saved = repository.list_framework_period_reviews(account_id)[0]
    assert saved.period_start == "2026-08-03"
    assert saved.period_end == "2026-08-09"
    assert saved.psychology_score == "100"


def test_roadmap_execution_gate_requires_score_sample_and_no_hard_rule(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)
    for level in (1, 2):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(account_id=account_id, pillar="psychology", level=level, item_key=item_key, completed=True, evidence_note="Documented evidence.")

    status = {item.pillar: item for item in service.roadmap_status(account_id)}["psychology"]
    execution_item = next(item for item in status.items if item.item_key == "execution")

    assert status.current_level == 3
    assert not execution_item.completed
    assert "20" in execution_item.evidence_summary


def test_roadmap_execution_gate_rounds_a_non_terminating_score_for_display(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(7):
        trade_id = _import_position(repository, account_id, position_id=f"seven-{index}", exit_time=f"2026-08-{index + 1:02d}T09:00:00+00:00")
        grades = {**ALL_PASS, "impulse_control": "fail"} if index == 0 else ALL_PASS
        tags = ("fomo_or_chase",) if index == 0 else ()
        action = "Wait for the written setup." if index == 0 else None
        _review(repository, account_id, trade_id, policy, strategy, grades=grades, tags=tags, action=action)
    for level in (1, 2):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(account_id=account_id, pillar="psychology", level=level, item_key=item_key, completed=True, evidence_note="Documented evidence.")

    status = {item.pillar: item for item in FrameworkService(repository).roadmap_status(account_id)}["psychology"]
    execution_item = next(item for item in status.items if item.item_key == "execution")

    assert status.current_level == 3
    assert "Score: 96" in execution_item.evidence_summary


def test_roadmap_measure_gate_requires_a_review_for_the_latest_completed_period(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(30):
        trade_id = _import_position(repository, account_id, position_id=f"measure-{index}")
        _review(repository, account_id, trade_id, policy, strategy)
    for level in (1, 2, 3):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(
                account_id=account_id,
                pillar="psychology",
                level=level,
                item_key=item_key,
                completed=True,
                evidence_note="Documented evidence.",
            )
    repository.save_framework_period_review(
        account_id=account_id,
        cadence="weekly",
        period_start="2026-08-03",
        period_end="2026-08-09",
        psychology_score="100",
        risk_score="100",
        system_score="100",
        readiness_score="100",
        alert_codes=(),
        recurring_issues=(),
        review_note="An older review must not unlock the next level.",
        priority_action="Continue the documented process.",
    )
    service = FrameworkService(repository)
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)

    before_current_review = {item.pillar: item for item in service.roadmap_status(account_id, now=now)}["psychology"]
    before_measure_item = next(item for item in before_current_review.items if item.item_key == "measure")

    assert before_current_review.current_level == 4
    assert not before_measure_item.completed

    service.save_period_review(
        account_id=account_id,
        cadence="weekly",
        review_note="The latest completed week was reviewed.",
        priority_action="Continue the documented process.",
        now=now,
    )

    after_current_review = {item.pillar: item for item in FrameworkService(repository).roadmap_status(account_id, now=now)}["psychology"]
    after_measure_item = next(item for item in after_current_review.items if item.item_key == "measure")

    assert after_measure_item.completed
    assert after_current_review.current_level == 5


def test_roadmap_measure_gate_uses_the_full_thirty_review_sample(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(30):
        trade_id = _import_position(repository, account_id, position_id=f"thirty-window-{index}")
        if index < 10:
            _review(
                repository,
                account_id,
                trade_id,
                policy,
                strategy,
                grades={
                    **ALL_PASS,
                    "rule_adherence": "fail",
                    "impulse_control": "fail",
                    "emotional_control": "fail",
                    "patience_discipline": "fail",
                },
                tags=("fomo_or_chase",),
                action="Follow the written rules before entering.",
            )
        else:
            _review(repository, account_id, trade_id, policy, strategy)
    for level in (1, 2, 3):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(
                account_id=account_id,
                pillar="psychology",
                level=level,
                item_key=item_key,
                completed=True,
                evidence_note="Documented evidence.",
            )

    service = FrameworkService(repository)
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    service.save_period_review(
        account_id=account_id,
        cadence="weekly",
        review_note="Reviewed the latest completed week.",
        priority_action="Continue the written process.",
        now=now,
    )
    twenty_review_score = {item.pillar: item for item in service.pillar_scores(account_id, window=20)}["psychology"]
    thirty_review_score = {item.pillar: item for item in service.pillar_scores(account_id, window=30)}["psychology"]
    status = {item.pillar: item for item in service.roadmap_status(account_id, now=now)}["psychology"]

    assert twenty_review_score.score == "100"
    assert Decimal(thirty_review_score.score) < Decimal("80")
    assert status.current_level == 4
    measure_item = next(item for item in status.items if item.item_key == "measure")
    assert not measure_item.completed


def test_saving_roadmap_evidence_through_the_service_rejects_a_locked_level(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)

    with pytest.raises(ValueError, match="not yet unlocked"):
        service.save_pillar_roadmap_evidence(
            account_id=account_id,
            pillar="psychology",
            level=2,
            item_key="practice",
            completed=True,
            evidence_note="Skipping ahead of level 1.",
        )


def test_saving_roadmap_evidence_through_the_service_rejects_an_auto_detected_item(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)

    with pytest.raises(ValueError, match="auto-detected"):
        service.save_pillar_roadmap_evidence(
            account_id=account_id,
            pillar="psychology",
            level=3,
            item_key="execution",
            completed=True,
            evidence_note="Should never be saved manually.",
        )


def test_roadmap_risk_policy_and_sizing_is_auto_detected_from_active_policy(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)
    before = next(item for item in service.roadmap_status(account_id) if item.pillar == "risk").items
    before_item = next(item for item in before if item.item_key == "policy_and_sizing")
    assert before_item.is_auto
    assert not before_item.completed

    _policy(repository, account_id)

    after = next(item for item in service.roadmap_status(account_id) if item.pillar == "risk").items
    after_item = next(item for item in after if item.item_key == "policy_and_sizing")
    assert after_item.completed
    assert "%/trade" in after_item.evidence_summary


def test_roadmap_system_items_ignore_an_unbound_strategy_profile(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)
    before = next(item for item in service.roadmap_status(account_id) if item.pillar == "system").items
    for key in ("rules", "examples", "backtest"):
        item = next(entry for entry in before if entry.item_key == key)
        assert item.is_auto
        assert not item.completed

    _strategy(repository)

    after = next(item for item in service.roadmap_status(account_id) if item.pillar == "system").items
    for key in ("rules", "examples", "backtest"):
        item = next(entry for entry in after if entry.item_key == key)
        assert not item.completed, key


def test_roadmap_hypothesis_is_auto_detected_from_a_resolved_framework_focus(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)
    before_item = next(item for item in service.roadmap_status(account_id) if item.pillar == "psychology").items
    hypothesis_item = next(item for item in before_item if item.item_key == "hypothesis")
    assert not hypothesis_item.completed

    focus = repository.save_framework_focus(
        account_id=None,
        pillar="psychology",
        metric_kind="manual_evidence",
        metric_code=None,
        hypothesis="Reducing revenge trades will raise rule adherence.",
        action_text="Pause 60 seconds after any loss before re-entering.",
        baseline_value="60",
        target_value="80",
        target_reviews=5,
        starting_manual_reviews=0,
    )
    repository.resolve_framework_focus(focus_id=focus.id, outcome="completed", resolution_note="Rule adherence rose to 85.")

    after_items = next(item for item in service.roadmap_status(account_id) if item.pillar == "psychology").items
    after_hypothesis_item = next(item for item in after_items if item.item_key == "hypothesis")
    assert after_hypothesis_item.completed
    assert "Rule adherence rose to 85." in after_hypothesis_item.evidence_summary


def test_roadmap_auto_items_never_write_a_persisted_evidence_row(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _strategy(repository)

    FrameworkService(repository).roadmap_status(account_id)

    assert repository.list_pillar_roadmap_evidence(account_id) == []


def test_saving_a_period_review_twice_for_the_same_period_is_rejected(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, exit_time="2026-08-03T09:00:00+00:00")
    _review(repository, account_id, trade_id, policy, strategy)
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    service = FrameworkService(repository)
    service.save_period_review(
        account_id=account_id,
        cadence="weekly",
        review_note="First save.",
        priority_action="Keep the same process.",
        now=now,
    )

    with pytest.raises(ValueError, match="already been saved"):
        repository.save_framework_period_review(
            account_id=account_id,
            cadence="weekly",
            period_start="2026-08-03",
            period_end="2026-08-09",
            psychology_score="0",
            risk_score="0",
            system_score="0",
            readiness_score="0",
            alert_codes=(),
            recurring_issues=(),
            review_note="Attempted rewrite.",
            priority_action="Should never be stored.",
        )


def test_only_psychology_accepts_a_period_review_saved_against_any_account(tmp_path) -> None:
    repository, primary_id = _repository(tmp_path)
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="1000",
    )
    secondary = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert secondary is not None
    primary_policy, secondary_policy = _policy(repository, primary_id), _policy(repository, secondary.id)
    strategy = _strategy(repository)
    for index in range(30):
        trade_id = _import_position(repository, primary_id, position_id=f"primary-{index}")
        _review(repository, primary_id, trade_id, primary_policy, strategy)
    for level in (1, 2, 3):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(account_id=primary_id, pillar="psychology", level=level, item_key=item_key, completed=True, evidence_note="Documented evidence.")
        for item_key, _ in ROADMAP_ITEMS["risk"][level]:
            repository.save_pillar_roadmap_evidence(account_id=primary_id, pillar="risk", level=level, item_key=item_key, completed=True, evidence_note="Documented evidence.")
        for item_key, _ in ROADMAP_ITEMS["system"][level]:
            repository.save_pillar_roadmap_evidence(account_id=primary_id, pillar="system", level=level, item_key=item_key, completed=True, evidence_note="Documented evidence.")
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)

    before = {item.pillar: item for item in FrameworkService(repository).roadmap_status(primary_id, now=now)}
    assert before["psychology"].current_level == 4
    assert before["risk"].current_level == 4
    assert before["system"].current_level == 4
    for pillar_status in before.values():
        measure_item = next(item for item in pillar_status.items if item.item_key == "measure")
        assert not measure_item.completed

    secondary_trade_id = _import_position(repository, secondary.id, position_id="secondary-only")
    _review(repository, secondary.id, secondary_trade_id, secondary_policy, strategy)
    FrameworkService(repository).save_period_review(
        account_id=secondary.id,
        cadence="weekly",
        review_note="Reviewed the secondary account's week.",
        priority_action="Keep the same process.",
        now=now,
    )

    after = {item.pillar: item for item in FrameworkService(repository).roadmap_status(primary_id, now=now)}
    psychology_measure = next(item for item in after["psychology"].items if item.item_key == "measure")
    risk_measure = next(item for item in after["risk"].items if item.item_key == "measure")
    system_measure = next(item for item in after["system"].items if item.item_key == "measure")
    assert psychology_measure.completed, "Psychology is trader-wide and should accept any account's period review"
    assert not risk_measure.completed, "Risk is account-scoped and must not unlock from another account's period review"
    assert not system_measure.completed, "System is account-scoped and must not unlock from another account's period review"


def test_corrections_archive_complete_prior_assessment(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    first = _review(repository, account_id, trade_id, policy, strategy)
    grades = {**ALL_PASS, "impulse_control": "partial"}
    corrected = _review(repository, account_id, trade_id, policy, strategy, grades=grades, tags=("fomo_or_chase",), action="Wait for the setup.")
    history = repository.list_post_trade_assessment_revisions(trade_id)

    assert first.version == 1
    assert corrected.version == 2
    assert history[0].criterion_grades["impulse_control"] == "pass"


def test_greenfield_database_rejects_legacy_framework_schema(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE post_trade_assessments (id INTEGER PRIMARY KEY, system_confirmed BOOLEAN)")

    with pytest.raises(JournalDatabaseResetRequiredError, match="greenfield three-pillar"):
        SQLiteJournalRepository(database).initialize()


def test_psychology_is_trader_wide_while_risk_and_system_are_account_scoped(tmp_path) -> None:
    repository, primary_id = _repository(tmp_path)
    repository.register_mt5_account(display_name="Secondary", login="654321", broker_server="DemoBroker-Live", account_currency="USD", export_file_path="", opening_balance="1000")
    secondary = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert secondary is not None
    primary_policy, secondary_policy = _policy(repository, primary_id), _policy(repository, secondary.id)
    strategy = _strategy(repository)
    primary_trade = _import_position(repository, primary_id, position_id="primary")
    _review(repository, primary_id, primary_trade, primary_policy, strategy)
    # The importer helper has a fixed export identity; create the second trade through the repository's existing account export handling.
    repository.upsert_mt5_positions(
        secondary.id,
        [
            MT5PositionExport(
                schema_version=3, account_login="654321", broker_server="DemoBroker-Live", account_currency="USD", position_id="secondary", symbol="XAUUSD", direction="long",
                entry_time="2026-08-10T08:00:00+00:00", exit_time="2026-08-10T09:00:00+00:00", entry_price="3300", exit_price="3320", volume="0.1",
                gross_pnl="20", commission="0", swap="0", fees="0", net_pnl="20", entry_deal_count=1,
            )
        ], "positions.csv", "secondary-hash"
    )
    secondary_trade = next(item.id for item in repository.list_closed_trades_for_review(secondary.id) if item.position_id == "secondary")
    _review(repository, secondary.id, secondary_trade, secondary_policy, strategy)

    scores = {item.pillar: item for item in FrameworkService(repository).pillar_scores(primary_id)}

    assert scores["psychology"].reviewed_total == 2
    assert scores["system"].reviewed_total == 1
    assert scores["risk"].reviewed_total == 1
    assert scores["psychology"].scope == "Trader-wide"
    assert scores["system"].scope == "Selected account"


def test_post_loss_discipline_uses_the_next_trader_wide_review(tmp_path) -> None:
    repository, primary_id = _repository(tmp_path)
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="1000",
    )
    secondary = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert secondary is not None
    primary_policy, secondary_policy = _policy(repository, primary_id), _policy(repository, secondary.id)
    strategy = _strategy(repository)
    primary_trade_id = _import_position(
        repository,
        primary_id,
        position_id="primary-loss",
        net_pnl="-20",
        exit_time="2026-08-10T09:00:00+00:00",
    )
    _review(repository, primary_id, primary_trade_id, primary_policy, strategy)
    repository.upsert_mt5_positions(
        secondary.id,
        [
            MT5PositionExport(
                schema_version=3,
                account_login="654321",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="secondary-after-loss",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T09:05:00+00:00",
                exit_time="2026-08-10T10:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
                entry_deal_count=1,
            )
        ],
        "positions.csv",
        "secondary-after-loss-hash",
    )
    secondary_trade_id = next(
        item.id for item in repository.list_closed_trades_for_review(secondary.id)
        if item.position_id == "secondary-after-loss"
    )
    _review(
        repository,
        secondary.id,
        secondary_trade_id,
        secondary_policy,
        strategy,
        grades={**ALL_PASS, "impulse_control": "fail"},
        tags=("post_loss_reset",),
        action="Pause after a loss before the next entry.",
    )

    psychology = {item.pillar: item for item in FrameworkService(repository).pillar_scores(primary_id)}["psychology"]

    assert psychology.component_scores[-1] == ("Post-loss discipline", "0")


def test_post_loss_discipline_scores_zero_for_a_tagged_reset_even_with_a_passing_grade(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    loss_trade_id = _import_position(
        repository,
        account_id,
        position_id="loss",
        net_pnl="-20",
        exit_time="2026-08-10T09:00:00+00:00",
    )
    _review(repository, account_id, loss_trade_id, policy, strategy)
    next_trade_id = _import_position(
        repository,
        account_id,
        position_id="after-loss",
        net_pnl="20",
        entry_time="2026-08-10T09:05:00+00:00",
        exit_time="2026-08-10T10:00:00+00:00",
    )
    _review(
        repository,
        account_id,
        next_trade_id,
        policy,
        strategy,
        grades=ALL_PASS,
        tags=("post_loss_reset",),
        action="Pause after a loss before the next entry.",
    )

    psychology = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}["psychology"]

    assert psychology.component_scores[-1] == ("Post-loss discipline", "0")


def test_deep_review_persists_controlled_context_and_reports_it(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    setup = repository.save_strategy_setup(strategy_profile_id=strategy.id, name="London pullback")
    session = repository.save_review_context_tag(kind="session", name="London")
    regime = repository.save_review_context_tag(kind="regime", name="Trending")
    trade_id = _import_position(repository, account_id)

    repository.save_post_trade_assessment(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id, strategy_profile_id=strategy.id,
        criterion_grades=ALL_PASS, violation_codes=(), hard_rule_codes=(), declared_actual_risk_amount="10",
        post_review_note="Reviewed.", corrective_action=None,
        review_context=ReviewContextSelection(setup.id, session.id, regime.id),
    )

    score = FrameworkService(repository).trade_process_scores(account_id)[0]
    assert (score.setup_snapshot, score.session_snapshot, score.regime_snapshot) == ("London pullback", "London", "Trending")
    assert FrameworkService(repository).context_breakdown(account_id, dimension="setup")[0].label == "London pullback"


def test_monitor_analysis_keeps_review_eligibility_and_joins_standard_r(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    auto_trade = _import_position(repository, account_id, position_id="auto", initial_risk_amount="5", exit_time="2026-08-10T09:00:00+00:00")
    manual_trade = _import_position(repository, account_id, position_id="manual", net_pnl="-20", exit_time="2026-08-11T09:00:00+00:00")
    before = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == auto_trade)
    repository.approve_auto_review(
        account_id=account_id, trade_id=auto_trade, risk_policy_id=policy.id,
        risk_evidence_source=before.risk_evidence_source, risk_policy_state=before.risk_policy_state,
        actual_risk_amount=before.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(before.risk_policy_state),
    )
    _review(repository, account_id, manual_trade, policy, strategy, tags=("revenge",), action="Pause after the loss.")

    report = FrameworkService(repository).monitor_analysis(
        account_id, start_date=date(2026, 8, 10), end_date=date(2026, 8, 11)
    )

    assert len(report.points) == 2
    assert len(report.reviewed_points) == 2
    assert {item.direction for item in report.reviewed_points} == {"long"}
    assert {item.outcome for item in report.reviewed_points} == {"profit", "loss"}
    assert {item.result_r for item in report.reviewed_points} == {"2", "-2"}
    assert {item.label: item.count for item in report.lifecycle} == {"manual_review": 1, "approved_auto_review": 1}
    assert report.issues[0].label == "revenge"
    assert report.issues[0].count == 1


def test_framework_focus_is_single_and_tracks_manual_reviews(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    first = _import_position(repository, account_id)
    _review(repository, account_id, first, policy, strategy)
    focus = repository.save_framework_focus(
        account_id=None, pillar="psychology", metric_kind="manual_evidence", metric_code=None,
        hypothesis="More deliberate review creates usable evidence.", action_text="Complete deep reviews.",
        baseline_value="1", target_value="5", target_reviews=5, starting_manual_reviews=1,
    )
    with pytest.raises(ValueError, match="Resolve the active"):
        repository.save_framework_focus(
            account_id=account_id, pillar="system", metric_kind="manual_evidence", metric_code=None,
            hypothesis="x", action_text="x", baseline_value=None, target_value="5", target_reviews=5, starting_manual_reviews=1,
        )
    current, progress = FrameworkService(repository).focus_progress(account_id)
    assert current == focus
    assert progress is not None and progress.reviews_completed == 0 and not progress.ready_to_evaluate


def test_system_focus_requires_an_account(tmp_path) -> None:
    repository, _account_id = _repository(tmp_path)

    with pytest.raises(ValueError, match="Trading system focus needs an account"):
        repository.save_framework_focus(
            account_id=None, pillar="system", metric_kind="manual_evidence", metric_code=None,
            hypothesis="Collect system evidence.", action_text="Review the next trade.", baseline_value="0", target_value="5",
            target_reviews=5, starting_manual_reviews=0,
        )


def test_system_focus_progress_uses_its_account_only(tmp_path) -> None:
    repository, primary_id = _repository(tmp_path)
    repository.register_mt5_account(
        display_name="Secondary", login="654321", broker_server="DemoBroker-Live", account_currency="USD", export_file_path="", opening_balance="1000",
    )
    secondary = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert secondary is not None
    primary_policy, secondary_policy = _policy(repository, primary_id), _policy(repository, secondary.id)
    strategy = _strategy(repository)
    first = _import_position(repository, primary_id, position_id="primary-focus-baseline")
    _review(repository, primary_id, first, primary_policy, strategy)
    focus = repository.save_framework_focus(
        account_id=primary_id, pillar="system", metric_kind="manual_evidence", metric_code=None,
        hypothesis="Collect account-specific system evidence.", action_text="Review the next trade.", baseline_value="1", target_value="5",
        target_reviews=5, starting_manual_reviews=1,
    )
    repository.upsert_mt5_positions(
        secondary.id,
        [MT5PositionExport(
            schema_version=3, account_login="654321", broker_server="DemoBroker-Live", account_currency="USD", position_id="secondary-focus", symbol="XAUUSD", direction="long",
            entry_time="2026-08-10T08:00:00+00:00", exit_time="2026-08-10T09:00:00+00:00", entry_price="3300", exit_price="3320", volume="0.1",
            gross_pnl="20", commission="0", swap="0", fees="0", net_pnl="20", entry_deal_count=1,
        )], "positions.csv", "secondary-focus-hash",
    )
    secondary_trade = next(item.id for item in repository.list_closed_trades_for_review(secondary.id) if item.position_id == "secondary-focus")
    _review(repository, secondary.id, secondary_trade, secondary_policy, strategy)

    current, progress = FrameworkService(repository).focus_progress(secondary.id)

    assert current == focus
    assert progress is not None and progress.reviews_completed == 0


def test_coach_creates_an_evidence_focus_from_the_same_reviewed_trade_predicate(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, _strategy_profile = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id, initial_risk_amount="5")
    score = FrameworkService(repository).trade_process_scores(account_id)[0]
    repository.approve_auto_review(
        account_id=account_id, trade_id=trade_id, risk_policy_id=policy.id,
        risk_evidence_source=score.risk_evidence_source, risk_policy_state=score.risk_policy_state,
        actual_risk_amount=score.actual_risk_amount,
        criterion_grades=FrameworkService._automatic_review_grades(score.risk_policy_state),
    )

    focus = FrameworkService(repository).ensure_coaching_focus(account_id)
    current, progress = FrameworkService(repository).focus_progress(account_id)

    assert focus is not None and focus.source == "coach" and focus.metric_kind == "manual_evidence"
    assert current == focus
    assert progress is not None and progress.reviews_completed == 0


def test_hard_rule_coaching_supersedes_an_active_focus(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    initial = _import_position(repository, account_id, position_id="initial")
    _review(repository, account_id, initial, policy, strategy)
    active = repository.save_framework_focus(
        account_id=None, pillar="psychology", metric_kind="manual_evidence", metric_code=None,
        hypothesis="x", action_text="x", baseline_value="1", target_value="5", target_reviews=5, starting_manual_reviews=1,
    )
    unsafe = _import_position(repository, account_id, position_id="unsafe")
    _review(repository, account_id, unsafe, policy, strategy, hard_rules=("stop_widened",), action="Keep the stop fixed.")

    focus = FrameworkService(repository).ensure_coaching_focus(account_id)

    assert focus is not None and focus.source == "coach" and focus.metric_code == "stop_widened"
    assert repository.get_active_framework_focus() == focus
    prior = next(item for item in repository.list_framework_focuses() if item.id == active.id)
    assert prior.status == "superseded"


def test_resolved_coach_focus_is_not_reopened_from_the_same_sample(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    _review(repository, account_id, trade_id, policy, strategy, hard_rules=("stop_widened",), action="Keep the stop fixed.")
    focus = FrameworkService(repository).ensure_coaching_focus(account_id)
    assert focus is not None
    repository.resolve_framework_focus(focus_id=focus.id, outcome="completed", resolution_note="Recorded the safety lesson.")

    assert FrameworkService(repository).ensure_coaching_focus(account_id) is None


def test_pending_coaching_reason_explains_the_same_weak_area_wait(tmp_path) -> None:
    """When ensure_coaching_focus() suppresses reopening the same recommendation (see the
    test above), the UI should still be able to explain why - not silently imply "on track".
    """
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    trade_id = _import_position(repository, account_id)
    _review(repository, account_id, trade_id, policy, strategy, hard_rules=("stop_widened",), action="Keep the stop fixed.")
    focus = FrameworkService(repository).ensure_coaching_focus(account_id)
    assert focus is not None
    repository.resolve_framework_focus(focus_id=focus.id, outcome="completed", resolution_note="Recorded the safety lesson.")

    assert FrameworkService(repository).ensure_coaching_focus(account_id) is None
    reason = FrameworkService(repository).pending_coaching_reason(account_id)
    assert reason is not None
    assert "Risk management" in reason or "Trading system" in reason or "Psychology" in reason


def test_pending_coaching_reason_is_none_when_genuinely_on_track(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    for index in range(5):
        trade_id = _import_position(repository, account_id, position_id=f"clean-{index}")
        _review(repository, account_id, trade_id, policy, strategy, action=None)

    assert FrameworkService(repository).coaching_recommendation(account_id) is None
    assert FrameworkService(repository).pending_coaching_reason(account_id) is None


def test_resolved_risk_focus_on_another_account_does_not_suppress_coaching(tmp_path) -> None:
    repository, primary_id = _repository(tmp_path)
    repository.register_mt5_account(
        display_name="Secondary", login="654321", broker_server="DemoBroker-Live", account_currency="USD", export_file_path="", opening_balance="1000",
    )
    secondary = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert secondary is not None
    primary_policy, secondary_policy = _policy(repository, primary_id), _policy(repository, secondary.id)
    strategy = _strategy(repository)
    previous = repository.save_framework_focus(
        account_id=primary_id, pillar="risk", metric_kind="violation", metric_code="stop_widened",
        hypothesis="x", action_text="x", baseline_value="1", target_value="0", target_reviews=5, starting_manual_reviews=0,
        source="coach", coach_reason="Hard-rule safety focus.",
    )
    repository.resolve_framework_focus(focus_id=previous.id, outcome="completed", resolution_note="Resolved on the primary account.")
    repository.upsert_mt5_positions(
        secondary.id,
        [MT5PositionExport(
            schema_version=3, account_login="654321", broker_server="DemoBroker-Live", account_currency="USD", position_id="secondary-unsafe", symbol="XAUUSD", direction="long",
            entry_time="2026-08-10T08:00:00+00:00", exit_time="2026-08-10T09:00:00+00:00", entry_price="3300", exit_price="3320", volume="0.1",
            gross_pnl="20", commission="0", swap="0", fees="0", net_pnl="20", entry_deal_count=1,
        )], "positions.csv", "secondary-unsafe-hash",
    )
    secondary_trade = next(item.id for item in repository.list_closed_trades_for_review(secondary.id) if item.position_id == "secondary-unsafe")
    _review(repository, secondary.id, secondary_trade, secondary_policy, strategy, hard_rules=("stop_widened",), action="Keep the stop fixed.")

    focus = FrameworkService(repository).ensure_coaching_focus(secondary.id)

    assert focus is not None and focus.pillar == "risk" and focus.account_id == secondary.id


def test_coach_keeps_the_actual_weak_component_and_excludes_pre_focus_reviews(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy, strategy = _policy(repository, account_id), _strategy(repository)
    tags = ("fomo_or_chase", "revenge", "emotional_sizing", "post_loss_reset", "daily_limit")
    for index, tag in enumerate(tags):
        trade_id = _import_position(repository, account_id, position_id=f"weak-{index}")
        _review(repository, account_id, trade_id, policy, strategy, grades={**ALL_PASS, "rule_adherence": "fail"}, tags=(tag,), action="Use the written rule.")

    recommendation = FrameworkService(repository).coaching_recommendation(account_id)
    focus = FrameworkService(repository).ensure_coaching_focus(account_id)
    _current, progress = FrameworkService(repository).focus_progress(account_id)

    assert recommendation is not None and recommendation.metric_kind == "component" and recommendation.metric_code == "rule_adherence"
    assert focus is not None and focus.baseline_value == "0"
    assert progress is not None and progress.reviews_completed == 0 and progress.current_value is None
