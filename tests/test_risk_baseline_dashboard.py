from __future__ import annotations

from dataclasses import replace
import sqlite3
from pathlib import Path

import pytest

from trading_journal.application.dashboard import DashboardService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import JournalDatabaseResetRequiredError, SQLiteJournalRepository


def _rebuild_without_columns(connection: sqlite3.Connection, table: str, excluded: set[str]) -> None:
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})") if row[1] not in excluded]
    selected = ", ".join(columns)
    connection.execute(f"CREATE TABLE {table}_legacy_shape AS SELECT {selected} FROM {table}")
    connection.execute(f"DROP TABLE {table}")
    connection.execute(f"ALTER TABLE {table}_legacy_shape RENAME TO {table}")


def position(
    position_id: str,
    *,
    net_pnl: str,
    exit_time: str,
    symbol: str = "XAUUSD",
    direction: str = "long",
    strategy: str | None = None,
) -> MT5PositionExport:
    return MT5PositionExport(
        schema_version=1,
        account_login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        position_id=position_id,
        symbol=symbol,
        direction=direction,
        entry_time="2026-08-01T08:00:00+00:00",
        exit_time=exit_time,
        entry_price="3300.00",
        exit_price="3310.00",
        volume="0.01",
        gross_pnl=net_pnl,
        commission="0",
        swap="0",
        fees="0",
        net_pnl=net_pnl,
    )


def configured_repository(tmp_path: Path, standard_risk_percent: str = "10") -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        opening_balance="100",
    )
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent=standard_risk_percent,
        maximum_risk_per_trade_percent=standard_risk_percent,
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )
    repository.upsert_mt5_positions(
        account.id,
        [
            position("1001", net_pnl="20", exit_time="2026-08-01T09:00:00+00:00"),
            position("1002", net_pnl="-5", exit_time="2026-08-02T09:00:00+00:00"),
        ],
        "positions.csv",
        "test-hash",
    )
    return repository


def test_fresh_journal_settings_schema_excludes_monthly_target(tmp_path: Path) -> None:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")

    connection = sqlite3.connect(tmp_path / "journal.db")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(journal_settings)")}
    connection.close()

    assert "monthly_target" not in columns
    assert not hasattr(repository.get_journal_settings(), "monthly_target")
    assert "default_planned_risk_amount" not in columns
    assert not hasattr(repository.get_journal_settings(), "default_planned_risk_amount")


def test_legacy_monthly_target_database_requires_reset(tmp_path: Path) -> None:
    database_path = tmp_path / "old-journal.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, base_currency VARCHAR(3) NOT NULL, reporting_timezone VARCHAR(64) NOT NULL, monthly_target VARCHAR NOT NULL)")
    connection.execute("INSERT INTO journal_settings VALUES (1, 'USD', 'UTC', '1000')")
    connection.commit()
    connection.close()

    repository = SQLiteJournalRepository(database_path)
    with pytest.raises(JournalDatabaseResetRequiredError, match="make reset-db CONFIRM_RESET=yes"):
        repository.initialize()

    connection = sqlite3.connect(database_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(journal_settings)")}
    row = connection.execute("SELECT base_currency, reporting_timezone, monthly_target FROM journal_settings").fetchone()
    connection.close()
    assert "monthly_target" in columns
    assert row == ("USD", "UTC", "1000")


def test_existing_account_schema_is_rejected_in_greenfield_mode(tmp_path: Path) -> None:
    database_path = tmp_path / "old-account.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, base_currency VARCHAR(3) NOT NULL, reporting_timezone VARCHAR(64) NOT NULL, default_planned_risk_amount VARCHAR, starting_balance VARCHAR, default_strategy_name VARCHAR(100), default_strategy_profile_id INTEGER)"
    )
    connection.execute("INSERT INTO journal_settings VALUES (1, 'USD', 'UTC', NULL, NULL, NULL, NULL)")
    connection.execute(
        "CREATE TABLE mt5_accounts (id INTEGER PRIMARY KEY, display_name VARCHAR(100) NOT NULL, login VARCHAR(64) NOT NULL, broker_server VARCHAR(255) NOT NULL, account_currency VARCHAR(3) NOT NULL, export_file_path VARCHAR(1024) NOT NULL, active BOOLEAN NOT NULL)"
    )
    connection.execute("INSERT INTO mt5_accounts VALUES (1, 'Primary', '123456', 'DemoBroker-Live', 'USD', '', 1)")
    connection.commit()
    connection.close()

    repository = SQLiteJournalRepository(database_path)
    with pytest.raises(JournalDatabaseResetRequiredError, match="greenfield three-pillar"):
        repository.initialize()


def test_existing_risk_policy_schema_is_rejected_in_greenfield_mode(tmp_path: Path) -> None:
    database_path = tmp_path / "old-risk-policy.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE account_risk_policies (
            id INTEGER PRIMARY KEY,
            mt5_account_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            active BOOLEAN NOT NULL,
            risk_per_trade_percent VARCHAR NOT NULL,
            daily_loss_limit_r VARCHAR NOT NULL,
            weekly_loss_limit_r VARCHAR NOT NULL,
            max_drawdown_percent VARCHAR NOT NULL,
            max_open_risk_r VARCHAR NOT NULL,
            max_consecutive_losses INTEGER NOT NULL,
            minimum_rr VARCHAR NOT NULL,
            correlation_policy VARCHAR,
            created_at VARCHAR NOT NULL,
            UNIQUE(mt5_account_id, version)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO account_risk_policies VALUES
        (1, 1, 1, 1, '0.5', '2', '4', '10', '1', 3, '1.5', NULL, '2026-08-11T00:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()

    repository = SQLiteJournalRepository(database_path)
    with pytest.raises(JournalDatabaseResetRequiredError, match="greenfield three-pillar"):
        repository.initialize()


def test_account_policy_supplies_r_and_preserves_imported_policy_context(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")

    before = {trade.position_id: trade for trade in repository.list_trades()}
    assert before["1001"].effective_risk == "10"
    assert before["1001"].risk_source == "Risk policy v1 standard risk"
    assert before["1001"].result_r == "2"
    assert before["1002"].effective_risk == "10"
    assert before["1002"].risk_source == "Risk policy v1 standard risk"
    assert before["1002"].result_r == "-0.5"

    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="20",
        maximum_risk_per_trade_percent="20",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        expected_active_policy_id=repository.get_active_risk_policy(account.id).id,
        confirm_recalculation=True,
    )
    repository.upsert_mt5_positions(
        account.id,
        [position("1003", net_pnl="20", exit_time="2026-08-03T09:00:00+00:00")],
        "positions.csv",
        "updated-policy-hash",
    )
    after = {trade.position_id: trade for trade in repository.list_trades()}
    assert after["1001"].result_r == "1"
    assert after["1002"].result_r == "-0.25"
    assert after["1003"].effective_risk == "20"
    assert after["1003"].risk_source == "Risk policy v2 standard risk"
    assert after["1003"].result_r == "1"


def test_replacing_active_policy_requires_current_confirmation(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    active = repository.get_active_risk_policy(account.id)
    assert active is not None
    preview = repository.preview_risk_policy_change(account.id)
    assert preview.expected_active_policy_id == active.id
    assert preview.affected_logical_trades == 2

    values = dict(
        account_id=account.id,
        standard_risk_per_trade_percent="20",
        maximum_risk_per_trade_percent="20",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )
    with pytest.raises(ValueError, match="Confirm recalculation"):
        repository.save_account_risk_policy(**values)
    with pytest.raises(ValueError, match="changed"):
        repository.save_account_risk_policy(
            **values,
            expected_active_policy_id=active.id + 999,
            confirm_recalculation=True,
        )


def test_identical_active_policy_is_a_no_op_without_confirmation(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    active = repository.get_active_risk_policy(account.id)
    assert active is not None
    values = dict(
        account_id=account.id,
        standard_risk_per_trade_percent="10.00",
        maximum_risk_per_trade_percent="10.0",
        daily_loss_limit_r="2.00",
        weekly_loss_limit_r="4.0",
        max_drawdown_percent="10.00",
        max_open_risk_r="1.0",
        max_consecutive_losses=3,
        minimum_rr="1.50",
        correlation_policy="  ",
    )

    assert not repository.risk_policy_change_required(**values)
    saved = repository.save_account_risk_policy(**values)

    assert saved.id == active.id
    assert saved.version == active.version
    assert len(repository.list_account_risk_policies(account.id)) == 1


def test_initialization_does_not_rewrite_existing_clean_trade_tables(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    database_path = tmp_path / "journal.db"
    connection = sqlite3.connect(database_path)
    for column_name, column_type in [
        ("strategy", "VARCHAR(100)"),
        ("strategy_profile_id", "INTEGER"),
        ("notes", "VARCHAR"),
        ("planned_risk_amount", "VARCHAR"),
        ("result_r", "VARCHAR"),
        ("journal_completed_at", "VARCHAR(64)"),
    ]:
        connection.execute(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}")
    connection.execute(
        "UPDATE trades SET strategy = 'Legacy', notes = 'Legacy note', planned_risk_amount = '5', result_r = '4', journal_completed_at = '2026-08-10T00:00:00+00:00'"
    )
    connection.commit()
    connection.close()

    repository.initialize()

    connection = sqlite3.connect(database_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(trades)")}
    connection.close()
    assert columns.issuperset({"strategy", "strategy_profile_id", "notes", "planned_risk_amount", "result_r", "journal_completed_at"})
    assert {trade.position_id: trade.result_r for trade in repository.list_trades()} == {"1001": "2", "1002": "-0.5"}


def test_initialization_migrates_monitoring_reset_periods_and_policy_server_offset(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    repository.close()
    database_path = tmp_path / "journal.db"
    connection = sqlite3.connect(database_path)
    connection.execute("UPDATE mt5_accounts SET latest_server_utc_offset_minutes = 180")
    for trigger in (
        "enforce_trade_account_insert",
        "enforce_trade_account_update",
        "enforce_assessment_account_insert",
        "enforce_assessment_account_update",
        "prevent_risk_policy_account_reassignment",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    _rebuild_without_columns(
        connection,
        "account_risk_policies",
        {"drawdown_reset_period", "loss_streak_reset_period", "server_utc_offset_minutes"},
    )
    connection.commit()
    connection.close()

    migrated = SQLiteJournalRepository(database_path)
    migrated.initialize()
    account = migrated.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    policy = migrated.get_active_risk_policy(account.id)

    assert policy is not None
    assert policy.drawdown_reset_period == "daily"
    assert policy.loss_streak_reset_period == "daily"
    assert policy.server_utc_offset_minutes == 180


def test_saved_policy_keeps_the_account_server_offset_snapshot(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [position("offset-180", net_pnl="1", exit_time="2026-08-03T09:00:00+00:00").model_copy(update={"server_utc_offset_minutes": 180})],
        "positions.csv",
        "offset-180-hash",
    )
    policy = repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="10",
        maximum_risk_per_trade_percent="10",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        expected_active_policy_id=repository.get_active_risk_policy(account.id).id,
        confirm_recalculation=True,
    )
    repository.upsert_mt5_positions(
        account.id,
        [position("offset-minus-300", net_pnl="1", exit_time="2026-08-04T09:00:00+00:00").model_copy(update={"server_utc_offset_minutes": -300})],
        "positions.csv",
        "offset-minus-300-hash",
    )

    preserved = repository.get_risk_policy(policy.id)

    assert preserved is not None
    assert preserved.server_utc_offset_minutes == 180


def test_first_sync_backfills_every_null_policy_offset_once(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="10",
        maximum_risk_per_trade_percent="10",
        daily_loss_limit_r="3",
        weekly_loss_limit_r="5",
        max_drawdown_percent="12",
        max_open_risk_r="1",
        max_consecutive_losses=4,
        minimum_rr="1.5",
        correlation_policy=None,
        expected_active_policy_id=repository.get_active_risk_policy(account.id).id,
        confirm_recalculation=True,
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE account_risk_policies SET server_utc_offset_minutes = NULL WHERE mt5_account_id = ?",
            (account.id,),
        )
    repository.upsert_mt5_positions(
        account.id,
        [position("first-offset-sync", net_pnl="1", exit_time="2026-08-05T09:00:00+00:00").model_copy(update={"server_utc_offset_minutes": 180})],
        "positions.csv",
        "first-offset-sync-hash",
    )

    policies = repository.list_account_risk_policies(account.id)

    assert len(policies) == 2
    assert {policy.server_utc_offset_minutes for policy in policies} == {180}


def test_risk_policy_rejects_unknown_monitoring_reset_period(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None

    with pytest.raises(ValueError, match="Drawdown reset period"):
        repository.save_account_risk_policy(
            account_id=account.id,
            standard_risk_per_trade_percent="10",
            maximum_risk_per_trade_percent="10",
            daily_loss_limit_r="2",
            weekly_loss_limit_r="4",
            max_drawdown_percent="10",
            max_open_risk_r="1",
            max_consecutive_losses=3,
            minimum_rr="1.5",
            correlation_policy=None,
            drawdown_reset_period="session",
        )


def test_dashboard_builds_kpis_and_time_series_from_effective_risk(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert report.trade_count == 2
    assert report.net_pnl == "15"
    assert report.total_r == "1.5"
    assert report.win_rate == "50"
    assert [point.cumulative_pnl for point in report.cumulative] == ["20", "15"]
    assert [point.cumulative_r for point in report.cumulative] == ["2", "1.5"]
    assert [(item.strategy, item.net_pnl, item.total_r) for item in report.by_strategy] == [("Journal default", "15", "1.5")]


def test_dashboard_separates_profit_and_loss_concentration_by_trade_symbol_and_strategy(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [
            position("1001", net_pnl="80", exit_time="2026-08-01T09:00:00+00:00", symbol="XAUUSD"),
            position("1002", net_pnl="20", exit_time="2026-08-02T09:00:00+00:00", symbol="EURUSD"),
            position("1003", net_pnl="-40", exit_time="2026-08-03T09:00:00+00:00", symbol="XAUUSD"),
            position("1004", net_pnl="-10", exit_time="2026-08-04T09:00:00+00:00", symbol="EURUSD"),
        ],
        "positions.csv",
        "concentration-hash",
    )

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")
    concentration = {item.dimension: item for item in report.concentration}

    trade_profit = concentration["trade"].profit
    assert trade_profit.gross_amount == "100"
    assert trade_profit.target_group_count == 1
    assert [(item.label, item.amount, item.share_percent, item.cumulative_share_percent) for item in trade_profit.items] == [
        ("LT-1 · #1001", "80", "80", "80"),
        ("LT-2 · #1002", "20", "20", "100"),
    ]
    assert [(item.label, item.amount) for item in concentration["trade"].loss.items] == [
        ("LT-3 · #1003", "40"),
        ("LT-4 · #1004", "10"),
    ]
    assert [(item.label, item.amount, item.trade_count) for item in concentration["symbol"].profit.items] == [
        ("XAUUSD", "80", 1),
        ("EURUSD", "20", 1),
    ]
    assert [(item.label, item.amount) for item in concentration["symbol"].loss.items] == [
        ("XAUUSD", "40"),
        ("EURUSD", "10"),
    ]
    assert [(item.label, item.amount, item.trade_count) for item in concentration["strategy"].profit.items] == [
        ("Journal default", "100", 2),
    ]
    assert [(item.label, item.amount, item.trade_count) for item in concentration["strategy"].loss.items] == [
        ("Journal default", "50", 2),
    ]


def test_dashboard_concentration_handles_profit_only_loss_only_and_breakeven_samples(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [
            position("1001", net_pnl="5", exit_time="2026-08-01T09:00:00+00:00"),
            position("1002", net_pnl="0", exit_time="2026-08-02T09:00:00+00:00"),
            position("1003", net_pnl="-7", exit_time="2026-09-01T09:00:00+00:00"),
            position("1004", net_pnl="0", exit_time="2026-09-02T09:00:00+00:00"),
        ],
        "positions.csv",
        "profit-only-hash",
    )

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")
    breakdown = {item.dimension: item for item in report.concentration}["symbol"]

    assert breakdown.profit.gross_amount == "5"
    assert breakdown.profit.target_group_percent == "100"
    assert breakdown.loss.gross_amount == "0"
    assert breakdown.loss.items == []
    assert report.payoff_ratio is None
    assert report.profit_factor is None
    assert report.recovery_factor is None

    loss_only = DashboardService(repository).build_report(start_date="2026-09-01", end_date="2026-09-30")
    loss_breakdown = {item.dimension: item for item in loss_only.concentration}["symbol"]
    assert loss_breakdown.profit.items == []
    assert loss_breakdown.loss.gross_amount == "7"
    assert loss_only.profit_factor == "0"
    assert loss_only.payoff_ratio is None
    assert loss_only.recovery_factor == "-1"

    breakeven_only = DashboardService(repository).build_report(start_date="2026-08-02", end_date="2026-08-02")
    breakeven_breakdown = {item.dimension: item for item in breakeven_only.concentration}["symbol"]
    assert breakeven_breakdown.profit.items == []
    assert breakeven_breakdown.loss.items == []
    assert breakeven_only.expectancy_r == "0"
    assert breakeven_only.current_streak_outcome == "breakeven"
    assert breakeven_only.current_streak_count == 1


def test_dashboard_expectancy_r_uses_only_trades_with_r_coverage(tmp_path: Path, monkeypatch) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    original = repository.list_trade_performance

    def partial_r_coverage(account_id=None):
        trades = original(account_id)
        return [replace(trades[0], result_r=None), trades[1]]

    monkeypatch.setattr(repository, "list_trade_performance", partial_r_coverage)

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert report.total_r == "-0.5"
    assert report.r_trade_count == 1
    assert report.expectancy_r == "-0.5"
    assert report.by_symbol[0].r_trade_count == 1
    assert report.by_symbol[0].expectancy_r == "-0.5"


def test_dashboard_collapses_equity_curve_to_one_point_per_day(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [position("1003", net_pnl="10", exit_time="2026-08-01T11:00:00+00:00")],
        "positions.csv",
        "test-hash",
    )

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert [point.date for point in report.cumulative] == ["2026-08-01", "2026-08-02"]
    assert [point.cumulative_pnl for point in report.cumulative] == ["30", "25"]
    assert [point.cumulative_r for point in report.cumulative] == ["3", "2.5"]


def test_dashboard_reports_end_of_day_drawdown_separately_from_trade_close_drawdown(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [
            position("1003", net_pnl="-10", exit_time="2026-08-03T09:00:00+00:00"),
            position("1004", net_pnl="6", exit_time="2026-08-03T10:00:00+00:00"),
        ],
        "positions.csv",
        "intraday-drawdown-hash",
    )

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert report.max_drawdown == "15"
    assert report.current_drawdown == "9"
    assert report.end_of_day_max_drawdown == "9"
    assert report.end_of_day_current_drawdown == "9"
    assert report.end_of_day_max_drawdown_percent == report.end_of_day_current_drawdown_percent


def test_dashboard_calculates_balance_growth_drawdown_and_trade_quality(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert report.starting_balance == "100"
    assert report.ending_balance == "115"
    assert report.balance_growth_percent == "15"
    assert report.max_drawdown == "5"
    assert report.current_drawdown == "5"
    assert report.max_drawdown_percent == "4.166666666666666666666666667"
    assert report.worst_day == "-5"
    assert report.profit_factor == "4"
    assert report.expectancy == "7.5"
    assert report.expectancy_r == "0.75"
    assert report.gross_profit == "20"
    assert report.gross_loss == "5"
    assert report.average_win == "20"
    assert report.average_loss == "-5"
    assert report.payoff_ratio == "4"
    assert (report.win_count, report.loss_count, report.breakeven_count) == (1, 1, 0)
    assert (report.active_day_count, report.profitable_day_count, report.profitable_day_rate) == (2, 1, "50")
    assert report.best_day == "20"
    assert report.average_day == "7.5"
    assert report.recovery_factor == "3"
    assert (report.current_streak_outcome, report.current_streak_count) == ("loss", 1)
    assert (report.longest_win_streak, report.longest_loss_streak) == (1, 1)
    assert [point.balance for point in report.cumulative] == ["120", "115"]
    assert [point.drawdown for point in report.cumulative] == ["0", "5"]
    assert [(point.position_id, point.net_pnl) for point in report.per_trade] == [("1001", "20"), ("1002", "-5")]
    assert [point.balance for point in report.per_trade] == ["120", "115"]
    assert [point.drawdown for point in report.per_trade] == ["0", "5"]
    assert [(item.label, item.trade_count, item.win_rate, item.net_pnl, item.total_r, item.expectancy_r, item.profit_factor) for item in report.by_symbol] == [
        ("XAUUSD", 2, "50", "15", "1.5", "0.75", "4"),
    ]
    assert [(item.label, item.trade_count, item.net_pnl) for item in report.by_direction] == [("long", 2, "15")]


def test_dashboard_maximum_percentage_drawdown_is_independent_of_maximum_amount(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [
            position("1003", net_pnl="85", exit_time="2026-08-03T09:00:00+00:00"),
            position("1004", net_pnl="-6", exit_time="2026-08-04T09:00:00+00:00"),
        ],
        "positions.csv",
        "drawdown-percent-hash",
    )

    report = DashboardService(repository).build_report()

    assert report.max_drawdown == "6"
    assert report.max_drawdown_percent == "4.166666666666666666666666667"


def test_dashboard_statistics_handle_breakevens_streaks_and_breakdowns(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [
            position("1003", net_pnl="-5", exit_time="2026-08-03T09:00:00+00:00", symbol="EURUSD", direction="short"),
            position("1004", net_pnl="0", exit_time="2026-08-04T09:00:00+00:00", symbol="EURUSD", direction="short"),
            position("1005", net_pnl="10", exit_time="2026-08-05T09:00:00+00:00", symbol="EURUSD", direction="long"),
            position("1006", net_pnl="5", exit_time="2026-08-06T09:00:00+00:00", symbol="XAUUSD", direction="long"),
        ],
        "positions.csv",
        "enriched-statistics-hash",
    )

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert (report.win_count, report.loss_count, report.breakeven_count) == (3, 2, 1)
    assert (report.current_streak_outcome, report.current_streak_count) == ("win", 2)
    assert (report.longest_win_streak, report.longest_loss_streak) == (2, 2)
    assert report.active_day_count == 6
    assert report.profitable_day_count == 3
    assert report.profitable_day_rate == "50"
    assert report.best_day == "20"
    assert report.worst_day == "-5"
    assert report.average_day == "4.166666666666666666666666667"
    assert [(item.label, item.trade_count, item.win_count, item.loss_count, item.breakeven_count) for item in report.by_symbol] == [
        ("EURUSD", 3, 1, 1, 1),
        ("XAUUSD", 3, 2, 1, 0),
    ]
    assert [(item.label, item.trade_count, item.win_count, item.loss_count) for item in report.by_direction] == [
        ("long", 4, 3, 1),
        ("short", 2, 0, 1),
    ]


def test_dashboard_statistics_report_unavailable_ratios_without_valid_denominators(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-01")

    assert report.loss_count == 0
    assert report.payoff_ratio is None
    assert report.profit_factor is None
    assert report.recovery_factor is None
    assert report.by_symbol[0].profit_factor is None


def test_dashboard_assigns_a_cross_period_logical_trade_to_its_final_close(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    logical_trades = repository.list_closed_trades_for_review(account.id)
    repository.create_logical_trade_group(
        account_id=account.id,
        logical_trade_ids=tuple(item.id for item in logical_trades),
        display_label="Cross-period scale",
    )

    report = DashboardService(repository).build_report(
        account_id=account.id,
        start_date="2026-08-02",
        end_date="2026-08-02",
    )

    assert report.net_pnl == "15"
    assert report.trade_count == 1
    assert report.gross_profit == "15"
    assert report.gross_loss == "0"
    assert (report.win_count, report.loss_count, report.breakeven_count) == (1, 0, 0)
    assert report.daily[0].date == "2026-08-02"


def test_dashboard_reports_only_the_selected_account_currency_and_trades(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")
    first_account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert first_account is not None
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="EUR",
        export_file_path="",
        opening_balance="500",
    )
    second_account = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert second_account is not None
    repository.upsert_mt5_positions(
        second_account.id,
        [position("2001", net_pnl="999", exit_time="2026-08-03T09:00:00+00:00")],
        "positions.csv",
        "second-account-hash",
    )

    report = DashboardService(repository).build_report(
        account_id=first_account.id,
        start_date="2026-08-01",
        end_date="2026-08-31",
    )

    assert report.trade_count == 2
    assert report.net_pnl == "15"
