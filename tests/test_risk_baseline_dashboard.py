from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_journal.application.dashboard import DashboardService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


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


def configured_repository(tmp_path: Path, baseline: str | None = "10") -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC", monthly_target="100", default_planned_risk_amount=baseline)
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
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


def test_existing_database_is_migrated_with_a_nullable_risk_baseline(tmp_path: Path) -> None:
    database_path = tmp_path / "old-journal.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, base_currency VARCHAR(3) NOT NULL, reporting_timezone VARCHAR(64) NOT NULL, monthly_target VARCHAR NOT NULL)")
    connection.execute("INSERT INTO journal_settings VALUES (1, 'USD', 'UTC', '1000')")
    connection.commit()
    connection.close()

    repository = SQLiteJournalRepository(database_path)
    repository.initialize()

    settings = repository.get_journal_settings()
    assert settings.default_planned_risk_amount is None
    assert settings.default_strategy_name is None
    assert database_path.with_suffix(".db.pre-risk-baseline.bak").exists()
    assert database_path.with_suffix(".db.pre-strategy-default.bak").exists()


def test_dynamic_baseline_recalculates_only_trades_without_an_override(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, baseline="10")
    repository.annotate_imported_trade(
        login="123456",
        broker_server="DemoBroker-Live",
        position_id="1002",
        strategy="Pullback",
        planned_risk_amount="5",
        notes=None,
    )

    before = {trade.position_id: trade for trade in repository.list_trades()}
    assert before["1001"].effective_risk == "10"
    assert before["1001"].risk_source == "Baseline"
    assert before["1001"].result_r == "2"
    assert before["1002"].effective_risk == "5"
    assert before["1002"].risk_source == "Override"
    assert before["1002"].result_r == "-1"

    repository.configure_journal(base_currency="USD", reporting_timezone="UTC", monthly_target="100", default_planned_risk_amount="20")
    after = {trade.position_id: trade for trade in repository.list_trades()}
    assert after["1001"].result_r == "1"
    assert after["1002"].result_r == "-1"


def test_dashboard_builds_kpis_and_time_series_from_effective_risk(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, baseline="10")

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31")

    assert report.trade_count == 2
    assert report.net_pnl == "15"
    assert report.total_r == "1.5"
    assert report.win_rate == "50"
    assert report.target_progress == "15"
    assert [point.cumulative_pnl for point in report.cumulative] == ["20", "15"]
    assert [point.cumulative_r for point in report.cumulative] == ["2", "1.5"]
    assert [(item.strategy, item.net_pnl, item.total_r) for item in report.by_strategy] == [("Untagged", "15", "1.5")]


def test_dashboard_collapses_equity_curve_to_one_point_per_day(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, baseline="10")
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


def test_dashboard_scales_the_target_to_each_calendar_month_in_the_period(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, baseline="10")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [position("1003", net_pnl="60", exit_time="2026-09-01T11:00:00+00:00")],
        "positions.csv",
        "test-hash",
    )

    report = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-09-30")

    assert report.net_pnl == "75"
    assert report.target_month_count == 2
    assert report.target_amount == "200"
    assert report.target_progress == "37.5"


def test_dashboard_calculates_balance_growth_drawdown_and_trade_quality(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, baseline="10")
    repository.configure_journal(
        base_currency="USD",
        reporting_timezone="UTC",
        monthly_target="100",
        default_planned_risk_amount="10",
        starting_balance="100",
    )

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
