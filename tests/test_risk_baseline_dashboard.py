from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trading_journal.application.dashboard import DashboardService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import JournalDatabaseResetRequiredError, SQLiteJournalRepository


def position(position_id: str, *, net_pnl: str, exit_time: str, strategy: str | None = None) -> MT5PositionExport:
    return MT5PositionExport(
        schema_version=1,
        account_login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        position_id=position_id,
        symbol="XAUUSD",
        direction="long",
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
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC")
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
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC")

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


def test_existing_account_is_migrated_with_a_nullable_balance_baseline(tmp_path: Path) -> None:
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
    repository.initialize()

    accounts = repository.list_mt5_accounts()
    assert accounts[0].opening_balance is None
    assert accounts[0].latest_mt5_balance is None
    assert database_path.with_suffix(".db.pre-account-balance.bak").exists()
    assert database_path.with_suffix(".db.pre-live-account-balance.bak").exists()


def test_existing_risk_policy_uses_its_prior_standard_as_the_new_maximum(tmp_path: Path) -> None:
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
    repository.initialize()

    connection = sqlite3.connect(database_path)
    value = connection.execute("SELECT maximum_risk_per_trade_percent FROM account_risk_policies WHERE id = 1").fetchone()[0]
    connection.close()
    assert value == "0.5"
    assert database_path.with_suffix(".db.pre-risk-policy-limit.bak").exists()


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
    )
    repository.upsert_mt5_positions(
        account.id,
        [position("1003", net_pnl="20", exit_time="2026-08-03T09:00:00+00:00")],
        "positions.csv",
        "updated-policy-hash",
    )
    after = {trade.position_id: trade for trade in repository.list_trades()}
    assert after["1001"].result_r == "2"
    assert after["1002"].result_r == "-0.5"
    assert after["1003"].effective_risk == "20"
    assert after["1003"].risk_source == "Risk policy v2 standard risk"
    assert after["1003"].result_r == "1"


def test_existing_trade_overrides_are_removed_during_initialization(tmp_path: Path) -> None:
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
    assert not columns.intersection({"strategy", "strategy_profile_id", "notes", "planned_risk_amount", "result_r", "journal_completed_at"})
    assert database_path.with_suffix(".db.pre-trade-override-removal.bak").exists()
    assert {trade.position_id: trade.result_r for trade in repository.list_trades()} == {"1001": "2", "1002": "-0.5"}


def test_dashboard_builds_kpis_and_time_series_from_effective_risk(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, standard_risk_percent="10")

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert report.trade_count == 2
    assert report.net_pnl == "15"
    assert report.total_r == "1.5"
    assert report.win_rate == "50"
    assert [point.cumulative_pnl for point in report.cumulative] == ["20", "15"]
    assert [point.cumulative_r for point in report.cumulative] == ["2", "1.5"]
    assert [(item.strategy, item.net_pnl, item.total_r) for item in report.by_strategy] == [("Untagged", "15", "1.5")]


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
    assert report.average_win == "20"
    assert report.average_loss == "-5"
    assert [point.balance for point in report.cumulative] == ["120", "115"]
    assert [point.drawdown for point in report.cumulative] == ["0", "5"]
    assert [(point.position_id, point.net_pnl) for point in report.per_trade] == [("1001", "20"), ("1002", "-5")]
    assert [point.balance for point in report.per_trade] == ["120", "115"]
    assert [point.drawdown for point in report.per_trade] == ["0", "5"]


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
