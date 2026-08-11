from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

import pytest

from trading_journal.application.framework import FrameworkService, ROADMAP_ITEMS
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import (
    ASSESSMENT_CRITERIA,
    JournalDatabaseResetRequiredError,
    SQLiteJournalRepository,
)


ALL_PASS = {criterion: "pass" for criterion in ASSESSMENT_CRITERIA}


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
        description="Trade a confirmed pullback continuation.",
        backtest_start_date="2024-01-01",
        backtest_end_date="2025-01-01",
        backtest_trade_count=120,
        backtest_win_rate="52",
        backtest_expectancy_r="0.25",
        backtest_net_r="30",
        backtest_notes="Representative sample including modeled costs.",
    )


def _import_position(
    repository: SQLiteJournalRepository,
    account_id: int,
    *,
    position_id: str = "1001",
    net_pnl: str = "20",
    exit_time: str = "2026-08-10T09:00:00+00:00",
    initial_risk_amount: str | None = None,
    initial_reward_amount: str | None = None,
    entry_stop_price: str | None = None,
    close_stop_price: str | None = None,
    account_balance: str | None = None,
) -> int:
    repository.upsert_mt5_positions(
        account_id,
        [
            MT5PositionExport(
                schema_version=3,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id=position_id,
                symbol="XAUUSD",
                direction="long",
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
                entry_deal_count=1,
                account_balance=account_balance,
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


def test_automatic_risk_evidence_is_advisory_and_never_creates_a_pillar_score(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    trade_id = _import_position(repository, account_id, initial_risk_amount="8", initial_reward_amount="16", entry_stop_price="3290")

    service = FrameworkService(repository)
    score = next(item for item in service.trade_process_scores(account_id) if item.trade_id == trade_id)
    pillars = service.pillar_scores(account_id)

    assert score.assessment_state == "automatic_evidence"
    assert score.auto_risk.state == "within_policy"
    assert score.risk_score is score.overall_score is None
    assert all(item.score is None for item in pillars)


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


def test_real_loss_and_live_balance_evidence_have_explicit_confidence(tmp_path) -> None:
    repository, account_id = _repository(tmp_path)
    _policy(repository, account_id)
    loser = _import_position(repository, account_id, position_id="loss", net_pnl="-8")
    winner = _import_position(repository, account_id, position_id="win", net_pnl="8", account_balance="1000")
    scores = {item.trade_id: item for item in FrameworkService(repository).trade_process_scores(account_id)}

    assert scores[loser].auto_risk.risk_basis == "real_loss_sl"
    assert scores[loser].auto_risk.confidence == "inferred"
    assert scores[winner].auto_risk.risk_basis == "live_account_balance_sl"
    assert scores[winner].auto_risk.confidence == "conservative"


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
    service.save_period_review(
        account_id=account_id, cadence="weekly", review_note="Stops widened twice.", priority_action="Use fixed stop orders.", now=datetime(2026, 8, 10, tzinfo=timezone.utc)
    )
    saved = repository.list_framework_period_reviews(account_id)

    assert len(saved) == 1
    assert saved[0].cadence == "weekly"
    assert saved[0].priority_action == "Use fixed stop orders."


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

    assert status.current_level == 3
    assert not status.can_complete_current_level
    assert "20 full reviews" in status.gate


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


def test_psychology_and_system_scores_are_trader_wide_while_risk_is_account_scoped(tmp_path) -> None:
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
    assert scores["system"].reviewed_total == 2
    assert scores["risk"].reviewed_total == 1
