from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

import pytest

from trading_journal.application.framework import FrameworkService, ROADMAP_ITEMS
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def _repository(tmp_path):
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="1000",
    )
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    return repository, account.id


def _policy(repository: SQLiteJournalRepository, account_id: int):
    return repository.save_account_risk_policy(
        account_id=account_id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )


def _strategy(repository: SQLiteJournalRepository):
    return repository.save_strategy_profile(
        name="Trend continuation",
        description="Trade a pullback only with a confirmed continuation.",
        backtest_start_date="2024-01-01",
        backtest_end_date="2025-01-01",
        backtest_trade_count=120,
        backtest_win_rate="52",
        backtest_expectancy_r="0.25",
        backtest_net_r="30",
        backtest_notes="Representative sample including costs.",
    )


def _import_position(
    repository: SQLiteJournalRepository,
    account_id: int,
    *,
    position_id="1001",
    net_pnl="20",
    exit_time="2026-08-10T09:00:00+00:00",
    schema_version=1,
    initial_risk_amount=None,
    initial_reward_amount=None,
    entry_stop_price=None,
    close_stop_price=None,
    entry_magic_number=None,
    direction="long",
    account_balance=None,
) -> int:
    repository.upsert_mt5_positions(
        account_id,
        [
            MT5PositionExport(
                schema_version=schema_version,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id=position_id,
                symbol="XAUUSD",
                direction=direction,
                entry_time="2026-08-10T08:00:00+00:00",
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
                entry_magic_number=entry_magic_number,
                entry_deal_count=1 if schema_version >= 2 else None,
                account_balance=account_balance,
            )
        ],
        "positions.csv",
        f"hash-{position_id}",
        live_account_balance=Decimal(account_balance) if account_balance is not None else None,
    )
    return next(item.id for item in repository.list_closed_trades_for_review(account_id) if item.position_id == position_id)


def _review(repository, account_id, trade_id, policy, strategy, *, actual_risk="10", system_confirmed=True, **violations):
    return repository.save_post_trade_assessment(
        account_id=account_id,
        trade_id=trade_id,
        risk_policy_id=policy.id,
        strategy_profile_id=strategy.id,
        system_confirmed=system_confirmed,
        system_failure_codes=() if system_confirmed else ("entry_trigger",),
        impulse_violation=violations.get("impulse", False),
        revenge_violation=violations.get("revenge", False),
        emotional_size_violation=violations.get("emotional_size", False),
        stop_widened_violation=violations.get("stop_widened", False),
        declared_actual_risk_amount=actual_risk,
        post_review_note="Reviewed against the process, independent of P&L.",
        corrective_action=violations.get("corrective_action"),
    )


def test_risk_monitoring_includes_every_imported_closed_position(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id, net_pnl="-20")

    snapshot = FrameworkService(repository).risk_snapshot(account_id, now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert snapshot.daily_r == "-1"
    assert snapshot.state == "clear"
    assert "1 closed position(s) still need a full review" in snapshot.message


def test_risk_monitoring_uses_chronological_trades_and_maximum_drawdown(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id, position_id="profit", net_pnl="100", exit_time="2026-08-08T09:00:00+00:00")
    _import_position(repository, account_id, position_id="loss", net_pnl="-150", exit_time="2026-08-09T09:00:00+00:00")
    _import_position(repository, account_id, position_id="recovery", net_pnl="200", exit_time="2026-08-10T09:00:00+00:00")

    snapshot = FrameworkService(repository).risk_snapshot(account_id, now=datetime(2026, 8, 11, tzinfo=timezone.utc))

    assert snapshot.current_drawdown_percent == "0"
    assert snapshot.max_drawdown_percent == "13.63636363636363636363636364"
    assert snapshot.consecutive_losses == 0
    assert snapshot.state == "stop"


def test_risk_monitoring_counts_the_latest_loss_streak(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id, position_id="profit", net_pnl="100", exit_time="2026-08-08T09:00:00+00:00")
    _import_position(repository, account_id, position_id="loss", net_pnl="-50", exit_time="2026-08-09T09:00:00+00:00")

    snapshot = FrameworkService(repository).risk_snapshot(account_id, now=datetime(2026, 8, 11, tzinfo=timezone.utc))

    assert snapshot.current_drawdown_percent == "4.545454545454545454545454545"
    assert snapshot.max_drawdown_percent == "4.545454545454545454545454545"
    assert snapshot.consecutive_losses == 1


def test_post_trade_assessment_is_attached_directly_to_one_imported_position(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id, net_pnl="-15")

    review = _review(repository, account_id, trade_id, policy, strategy, actual_risk="5")
    snapshot = FrameworkService(repository).risk_snapshot(account_id, now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert review.trade_id == trade_id
    assert repository.get_post_trade_assessment_for_trade(trade_id) == review
    assert snapshot.daily_r == "-3"
    assert snapshot.state == "stop"


def test_post_trade_review_rejects_another_accounts_position(tmp_path) -> None:
    repository, first_account_id = _repository(tmp_path)
    policy = _policy(repository, first_account_id)
    strategy = _strategy(repository)
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="1000",
    )
    second = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert second is not None
    trade_id = _import_position(repository, second.id)

    with pytest.raises(ValueError, match="not found for this account"):
        _review(repository, first_account_id, trade_id, policy, strategy)


def test_reviewed_trade_scores_are_available_before_the_roadmap_sample_is_complete(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    _review(repository, account_id, _import_position(repository, account_id), policy, strategy)

    scores = FrameworkService(repository).pillar_scores(account_id)

    assert all(score.score == "100" for score in scores)
    assert all(score.reviewed_count == 1 for score in scores)
    assert all(score.unreviewed_count == 0 for score in scores)
    assert all("Roadmap evidence still requires 10+ reviewed trades" in score.detail for score in scores)


def test_only_real_loss_sl_losses_receive_a_risk_only_auto_review(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id, position_id="winner", net_pnl="200")
    _import_position(repository, account_id, position_id="loser", net_pnl="-20")

    service = FrameworkService(repository)
    trade_scores = service.trade_process_scores(account_id)
    pillar_scores = service.pillar_scores(account_id)

    by_trade = {score.trade_id: score for score in trade_scores}
    winner = next(score for score in by_trade.values() if score.auto_risk.real_loss_sl_amount is None)
    loser = next(score for score in by_trade.values() if score.auto_risk.real_loss_sl_amount is not None)

    assert winner.assessment_state == "not_scored"
    assert winner.overall_score is None
    assert winner.psychology_score is winner.risk_score is winner.system_score is None
    assert loser.assessment_state == "auto_reviewed"
    assert loser.risk_score == "0"
    assert loser.psychology_score is loser.system_score is loser.overall_score is None
    assert all(score.score is None for score in pillar_scores)
    risk = next(score for score in pillar_scores if score.pillar == "risk")
    assert risk.reviewed_total == 0 and risk.unreviewed_total == 1
    assert risk.auto_reviewed_total == 1
    assert all(score.reviewed_total == 0 for score in pillar_scores)


def test_mt5_initial_risk_is_automatically_checked_without_using_pnl(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(
        repository,
        account_id,
        schema_version=2,
        net_pnl="200",
        initial_risk_amount="10",
        initial_reward_amount="20",
        entry_stop_price="3290",
        close_stop_price="3280",
    )

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "auto_reviewed"
    assert score.risk_score == "100"
    assert score.overall_score is None
    assert score.auto_risk.state == "within_policy"
    assert score.auto_risk.initial_rr == "2"
    assert score.auto_risk.observed_stop_widened is True


def test_review_register_risk_amounts_keep_policy_and_actual_risk_separate(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id, schema_version=2, initial_risk_amount="8")

    automatic_score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert automatic_score.policy_risk_amount == "10"
    assert automatic_score.actual_risk_amount == "8"

    _review(repository, account_id, trade_id, policy, strategy, actual_risk="6")
    reviewed_score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert reviewed_score.policy_risk_amount == "10"
    assert reviewed_score.actual_risk_amount == "6"


def test_unconfigured_automatic_risk_evidence_remains_in_needs_review(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    trade_id = _import_position(repository, account_id, schema_version=2, initial_risk_amount="10")

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "not_scored"
    assert score.risk_score is None
    assert score.auto_risk.state == "unavailable"
    assert score.auto_risk.risk_basis == "specific_preset_sl"


def test_automatic_risk_alert_uses_initial_risk_not_the_trade_result(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(repository, account_id, schema_version=2, net_pnl="200", initial_risk_amount="10.01")

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "auto_reviewed"
    assert score.risk_score == "0"
    assert score.auto_risk.state == "over_policy"
    assert score.overall_score is None


def test_loss_without_mt5_initial_risk_is_marked_as_real_loss_sl(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(repository, account_id, schema_version=2, net_pnl="-8.50")

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "auto_reviewed"
    assert score.risk_score == "100"
    assert score.overall_score is None
    assert score.auto_risk.state == "within_policy"
    assert score.auto_risk.risk_basis == "real_loss_sl"
    assert score.auto_risk.real_loss_sl_amount == "8.5"
    assert score.auto_risk.specific_preset_sl_amount is None
    assert "not an MT5-recorded initial stop" in score.auto_risk.detail


def test_review_uses_real_loss_sl_when_actual_risk_is_not_declared(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id, schema_version=2, net_pnl="-8")
    _review(repository, account_id, trade_id, policy, strategy, actual_risk=None)

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.risk_score == "100"


def test_real_loss_sl_auto_review_never_advances_the_roadmap(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    _import_position(repository, account_id, schema_version=2, net_pnl="-8")

    service = FrameworkService(repository)
    risk = next(item for item in service.pillar_scores(account_id) if item.pillar == "risk")
    roadmap = {item.pillar: item for item in service.roadmap_status(account_id)}

    assert risk.score is None
    assert risk.reviewed_total == 0
    assert risk.auto_reviewed_total == 1
    assert roadmap["risk"].gate == "Complete the current level evidence items."


def test_profitable_trade_without_an_entry_stop_uses_live_account_balance_sl(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(
        repository,
        account_id,
        schema_version=3,
        net_pnl="200",
        account_balance="1000",
    )

    service = FrameworkService(repository)
    score = next(item for item in service.trade_process_scores(account_id) if item.trade_id == trade_id)
    snapshot = service.risk_snapshot(account_id, now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert score.assessment_state == "auto_reviewed"
    assert score.risk_score == "0"
    assert score.overall_score is None
    assert score.auto_risk.risk_basis == "live_account_balance_sl"
    assert score.auto_risk.live_account_balance_sl_amount == "1000"
    assert snapshot.daily_r == "0.2"


def test_latest_live_account_balance_recalculates_eligible_auto_review_and_r_monitoring(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(
        repository,
        account_id,
        schema_version=3,
        net_pnl="200",
        account_balance="1000",
    )
    _import_position(
        repository,
        account_id,
        position_id="1001",
        schema_version=3,
        net_pnl="200",
        account_balance="2000",
    )

    service = FrameworkService(repository)
    score = next(item for item in service.trade_process_scores(account_id) if item.trade_id == trade_id)
    snapshot = service.risk_snapshot(account_id, now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert score.auto_risk.live_account_balance_sl_amount == "2000"
    assert snapshot.daily_r == "0.1"


def test_specific_preset_sl_takes_precedence_over_live_account_balance_sl(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(
        repository,
        account_id,
        schema_version=3,
        net_pnl="200",
        initial_risk_amount="10",
        entry_stop_price="3290",
        account_balance="1000",
    )

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "auto_reviewed"
    assert score.risk_score == "100"
    assert score.auto_risk.risk_basis == "specific_preset_sl"
    assert score.auto_risk.specific_preset_sl_amount == "10"
    assert score.auto_risk.live_account_balance_sl_amount is None


def test_recorded_entry_stop_without_calculated_risk_does_not_use_live_account_balance_sl(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(
        repository,
        account_id,
        schema_version=3,
        net_pnl="20",
        entry_stop_price="3290",
        account_balance="1000",
    )

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "not_scored"
    assert score.auto_risk.risk_basis == "unavailable"
    assert score.auto_risk.live_account_balance_sl_amount is None


def test_review_uses_mt5_initial_risk_when_actual_risk_is_not_declared(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id, schema_version=2, initial_risk_amount="10")
    _review(repository, account_id, trade_id, policy, strategy, actual_risk=None)

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.risk_score == "100"


def test_review_uses_its_attached_policy_risk_when_no_sl_source_or_actual_risk_exists(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id, schema_version=1, net_pnl="20")
    _review(repository, account_id, trade_id, policy, strategy, actual_risk=None)

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.risk_score == "100"


def test_standard_risk_normalizes_dashboard_r_while_maximum_risk_controls_compliance(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = repository.save_account_risk_policy(
        account_id=account_id,
        standard_risk_per_trade_percent="0.5",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )
    trade_id = _import_position(repository, account_id, schema_version=2, initial_risk_amount="8")

    imported = {trade.position_id: trade for trade in repository.list_trades()}["1001"]
    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert policy.standard_risk_per_trade_percent == "0.5"
    assert policy.maximum_risk_per_trade_percent == "1"
    assert imported.effective_risk == "5.0"
    assert imported.result_r == "4"
    assert score.policy_risk_amount == "10"
    assert score.actual_risk_amount == "8"
    assert score.assessment_state == "auto_reviewed"
    assert score.risk_score == "100"


def test_risk_policy_rejects_a_maximum_below_standard_risk(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)

    with pytest.raises(ValueError, match="at least the standard risk"):
        repository.save_account_risk_policy(
            account_id=account_id,
            standard_risk_per_trade_percent="1",
            maximum_risk_per_trade_percent="0.5",
            daily_loss_limit_r="2",
            weekly_loss_limit_r="4",
            max_drawdown_percent="10",
            max_open_risk_r="1",
            max_consecutive_losses=3,
            minimum_rr="1.5",
            correlation_policy=None,
        )


def test_risk_snapshot_uses_the_saved_reviews_policy_risk_after_a_policy_change(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    first_policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id, schema_version=1, net_pnl="20")
    _review(repository, account_id, trade_id, first_policy, strategy, actual_risk=None)
    repository.save_account_risk_policy(
        account_id=account_id,
        standard_risk_per_trade_percent="0.5",
        maximum_risk_per_trade_percent="0.5",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )

    snapshot = FrameworkService(repository).risk_snapshot(account_id, now=datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert snapshot.daily_r == "2"


def test_non_positive_live_balance_is_not_used_as_an_sl_source(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(
        repository,
        account_id,
        schema_version=3,
        net_pnl="20",
        account_balance="0",
    )

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.assessment_state == "not_scored"
    assert score.auto_risk.risk_basis == "unavailable"
    assert score.auto_risk.live_account_balance_sl_amount is None


def test_automatic_risk_review_retains_its_first_imported_policy_version(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    first_policy = _policy(repository, account_id)
    trade_id = _import_position(repository, account_id, schema_version=2, initial_risk_amount="10")
    repository.save_account_risk_policy(
        account_id=account_id,
        standard_risk_per_trade_percent="0.5",
        maximum_risk_per_trade_percent="0.5",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.auto_risk.policy_version == first_policy.version
    assert score.risk_score == "100"


def test_mt5_magic_number_maps_a_strategy_without_confirming_its_setup(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    strategy = _strategy(repository)
    repository.save_strategy_profile(
        name=strategy.name,
        description=strategy.description,
        backtest_start_date=strategy.backtest_start_date,
        backtest_end_date=strategy.backtest_end_date,
        backtest_trade_count=strategy.backtest_trade_count,
        backtest_win_rate=strategy.backtest_win_rate,
        backtest_expectancy_r=strategy.backtest_expectancy_r,
        backtest_net_r=strategy.backtest_net_r,
        backtest_notes=strategy.backtest_notes,
        magic_numbers="4242",
        strategy_id=strategy.id,
    )
    trade_id = _import_position(repository, account_id, schema_version=2, entry_magic_number="4242")

    score = next(item for item in FrameworkService(repository).trade_process_scores(account_id) if item.trade_id == trade_id)

    assert score.mapped_strategy is not None
    assert score.mapped_strategy.id == strategy.id
    assert score.assessment_state == "not_scored"


def test_review_creates_score_and_unreviewed_import_is_excluded_from_average(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    reviewed_trade_id = _import_position(repository, account_id, position_id="reviewed")
    _import_position(repository, account_id, position_id="unreviewed")
    _review(repository, account_id, reviewed_trade_id, policy, strategy)

    service = FrameworkService(repository)
    trade_scores = {score.trade_id: score for score in service.trade_process_scores(account_id)}
    scores = service.pillar_scores(account_id)

    assert trade_scores[reviewed_trade_id].assessment_state == "reviewed"
    assert trade_scores[reviewed_trade_id].overall_score == "100"
    assert all(score.score == "100" for score in scores)
    assert all(score.reviewed_count == 1 and score.unreviewed_count == 1 for score in scores)


def test_rebased_balance_recalculates_historical_risk_score(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    for sequence in range(10):
        trade_id = _import_position(repository, account_id, position_id=f"score-{sequence}", net_pnl="0")
        _review(repository, account_id, trade_id, policy, strategy, actual_risk="10")

    before = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}["risk"]
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="500",
    )
    after = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}["risk"]

    assert before.score == "100"
    assert after.score == "30"


def test_system_score_remains_separate_from_stop_discipline(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    for sequence in range(10):
        trade_id = _import_position(repository, account_id, position_id=f"review-{sequence}")
        _review(repository, account_id, trade_id, policy, strategy, stop_widened=sequence == 0)

    scores = {item.pillar: item for item in FrameworkService(repository).pillar_scores(account_id)}

    assert scores["risk"].score == "97"
    assert scores["system"].score == "100"


def test_roadmap_requires_post_trade_evidence_before_execution_level(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)
    for level in (1, 2):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(
                pillar="psychology",
                level=level,
                item_key=item_key,
                completed=True,
                evidence_note="Recorded in the local journal.",
            )

    level_three = {item.pillar: item for item in service.roadmap_status(account_id)}["psychology"]

    assert level_three.current_level == 3
    assert not level_three.can_complete_current_level
    assert "20 post-trade reviews" in level_three.gate


def test_unreviewed_imports_do_not_unlock_a_roadmap_gate(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    service = FrameworkService(repository)
    for sequence in range(20):
        _import_position(repository, account_id, position_id=f"unreviewed-{sequence}")
    for level in (1, 2):
        for item_key, _ in ROADMAP_ITEMS["psychology"][level]:
            repository.save_pillar_roadmap_evidence(
                pillar="psychology",
                level=level,
                item_key=item_key,
                completed=True,
                evidence_note="Recorded in the local journal.",
            )

    level_three = {item.pillar: item for item in service.roadmap_status(account_id)}["psychology"]

    assert level_three.current_level == 3
    assert not level_three.can_complete_current_level
    assert "20 post-trade reviews" in level_three.gate


def test_post_trade_assessment_keeps_strategy_snapshot_and_failure_evidence(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id)
    review = _review(repository, account_id, trade_id, policy, strategy, system_confirmed=False)

    repository.save_strategy_profile(
        name="Trend continuation",
        description=None,
        backtest_start_date=None,
        backtest_end_date=None,
        backtest_trade_count=None,
        backtest_win_rate=None,
        backtest_expectancy_r=None,
        backtest_net_r=None,
        backtest_notes=None,
        strategy_id=strategy.id,
    )
    saved = repository.get_post_trade_assessment_for_trade(trade_id)

    assert saved is not None
    assert saved.id == review.id
    assert saved.strategy_snapshot.description == "Trade a pullback only with a confirmed continuation."
    assert saved.system_failure_codes == ("entry_trigger",)


def test_correcting_a_review_archives_the_prior_version(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    policy = _policy(repository, account_id)
    strategy = _strategy(repository)
    trade_id = _import_position(repository, account_id)
    first = _review(repository, account_id, trade_id, policy, strategy, actual_risk="10")

    corrected = _review(repository, account_id, trade_id, policy, strategy, actual_risk="12", impulse=True)
    history = repository.list_post_trade_assessment_revisions(trade_id)

    assert first.version == 1
    assert corrected.version == 2
    assert len(history) == 1
    assert history[0].version == 1
    assert history[0].declared_actual_risk_amount == "10"
    assert not history[0].impulse_violation


def test_repository_exposes_post_trade_reviews_only(tmp_path) -> None:
    repository, _ = _repository(tmp_path)

    assert not hasattr(repository, "start_trading_session")
    assert not hasattr(repository, "create_trade_assessment")
    assert not hasattr(repository, "link_trade_assessment")


def test_existing_post_trade_reviews_gain_a_version_column_safely(tmp_path) -> None:
    database_path = tmp_path / "legacy-review.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE post_trade_assessments (
                id INTEGER NOT NULL PRIMARY KEY,
                mt5_account_id INTEGER NOT NULL,
                trade_id INTEGER NOT NULL,
                risk_policy_id INTEGER,
                strategy_profile_id INTEGER NOT NULL,
                strategy_snapshot TEXT NOT NULL,
                system_confirmed BOOLEAN NOT NULL,
                system_failure_codes TEXT NOT NULL,
                impulse_violation BOOLEAN NOT NULL,
                revenge_violation BOOLEAN NOT NULL,
                emotional_size_violation BOOLEAN NOT NULL,
                stop_widened_violation BOOLEAN NOT NULL,
                declared_actual_risk_amount VARCHAR,
                post_review_note TEXT NOT NULL,
                corrective_action TEXT,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            );
            """
        )

    SQLiteJournalRepository(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(post_trade_assessments)")}
    assert "version" in columns
    assert database_path.with_suffix(".db.pre-review-versioning.bak").exists()


def test_existing_trade_table_gains_auto_evidence_columns_safely(tmp_path) -> None:
    database_path = tmp_path / "legacy-trades.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                id INTEGER NOT NULL PRIMARY KEY,
                source VARCHAR(10) NOT NULL,
                mt5_account_id INTEGER,
                mt5_position_id VARCHAR(64),
                source_updated_at VARCHAR(64) NOT NULL,
                symbol VARCHAR(32) NOT NULL,
                direction VARCHAR(8) NOT NULL,
                entry_time VARCHAR(64) NOT NULL,
                exit_time VARCHAR(64) NOT NULL,
                entry_price VARCHAR NOT NULL,
                exit_price VARCHAR NOT NULL,
                volume VARCHAR NOT NULL,
                gross_pnl VARCHAR NOT NULL,
                commission VARCHAR NOT NULL,
                swap VARCHAR NOT NULL,
                fees VARCHAR NOT NULL,
                net_pnl VARCHAR NOT NULL
            );
            """
        )

    SQLiteJournalRepository(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(trades)")}
    assert {"entry_stop_price", "entry_magic_number", "initial_risk_amount", "auto_risk_policy_id"}.issubset(columns)
    assert database_path.with_suffix(".db.pre-auto-evidence.bak").exists()


def test_roadmap_evidence_is_trader_wide_except_for_risk(tmp_path) -> None:
    repository, first_account_id = _repository(tmp_path)
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="2000",
    )
    second = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert second is not None
    repository.save_pillar_roadmap_evidence(
        account_id=first_account_id,
        pillar="psychology",
        level=1,
        item_key="triggers",
        completed=True,
        evidence_note="Defined personal triggers.",
    )
    repository.save_pillar_roadmap_evidence(
        account_id=first_account_id,
        pillar="risk",
        level=1,
        item_key="policy",
        completed=True,
        evidence_note="Defined limits for the primary account.",
    )

    first_evidence = {(item.pillar, item.item_key) for item in repository.list_pillar_roadmap_evidence(first_account_id)}
    second_evidence = {(item.pillar, item.item_key) for item in repository.list_pillar_roadmap_evidence(second.id)}

    assert ("psychology", "triggers") in first_evidence
    assert ("psychology", "triggers") in second_evidence
    assert ("risk", "policy") in first_evidence
    assert ("risk", "policy") not in second_evidence
