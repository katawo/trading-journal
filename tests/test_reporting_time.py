from __future__ import annotations

import csv
from datetime import timedelta, timezone
from pathlib import Path

from trading_journal.application.dashboard import DashboardService
from trading_journal.application.import_mt5 import MT5ImportService
from trading_journal.application.reporting_time import reporting_date
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


V5_HEADER = [
    "schema_version", "account_login", "broker_server", "account_currency", "position_id", "symbol", "direction",
    "entry_time", "exit_time", "server_utc_offset_minutes", "entry_price", "exit_price", "volume", "gross_pnl",
    "commission", "swap", "fees", "net_pnl", "entry_stop_price", "entry_target_price", "close_stop_price",
    "entry_magic_number", "entry_deal_count", "exit_reason", "initial_risk_amount", "initial_reward_amount", "account_balance", "pretrade_account_balance",
]


def _write_v5_export(path: Path, *, currency: str = "EUR", server_offset: str = "180") -> None:
    row = {
        "schema_version": "5", "account_login": "123456", "broker_server": "DemoBroker-Live", "account_currency": currency,
        "position_id": "9001", "symbol": "EURUSD", "direction": "long", "entry_time": "2026-08-10T00:00:00",
        "exit_time": "2026-08-10T00:30:00", "server_utc_offset_minutes": server_offset, "entry_price": "1.10000",
        "exit_price": "1.10100", "volume": "1.00", "gross_pnl": "100.00", "commission": "-1.50", "swap": "-0.25",
        "fees": "-0.25", "net_pnl": "98.00", "entry_stop_price": "", "entry_target_price": "",
        "close_stop_price": "", "entry_magic_number": "", "entry_deal_count": "", "exit_reason": "client",
        "initial_risk_amount": "", "initial_reward_amount": "", "account_balance": "1000.00", "pretrade_account_balance": "",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=V5_HEADER)
        writer.writeheader()
        writer.writerow(row)


def _repository(tmp_path: Path) -> tuple[SQLiteJournalRepository, int]:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.register_mt5_account(
        display_name="Euro account", login="123456", broker_server="DemoBroker-Live", account_currency="EUR", export_file_path=""
    )
    return repository, repository.list_mt5_accounts()[0].id


def test_schema_v4_uses_account_currency_and_preserves_original_server_clock(tmp_path: Path) -> None:
    repository, account_id = _repository(tmp_path)
    export_path = tmp_path / "positions.csv"
    _write_v5_export(export_path)

    MT5ImportService(repository).import_csv(export_path)
    trade = repository.list_closed_trades_for_review(account_id)[0]

    assert trade.exit_time == "2026-08-09T21:30:00+00:00"
    assert trade.server_utc_offset_minutes == 180
    assert repository.get_latest_mt5_balance(account_id) == "1000.00"

    _write_v5_export(export_path, server_offset="240")
    MT5ImportService(repository).import_csv(export_path)
    preserved = repository.list_closed_trades_for_review(account_id)[0]
    assert preserved.exit_time == "2026-08-09T21:30:00+00:00"
    assert preserved.server_utc_offset_minutes == 180


def test_reporting_basis_changes_the_dashboard_calendar_without_currency_conversion(tmp_path: Path) -> None:
    repository, account_id = _repository(tmp_path)
    export_path = tmp_path / "positions.csv"
    _write_v5_export(export_path)
    MT5ImportService(repository).import_csv(export_path)
    dashboard = DashboardService(repository)

    repository.configure_journal(reporting_time_basis="utc")
    assert dashboard.earliest_trade_date(account_id).isoformat() == "2026-08-09"

    repository.configure_journal(reporting_time_basis="server")
    report = dashboard.build_report(account_id=account_id, start_date="2026-08-10", end_date="2026-08-10")
    assert report.trade_count == 1
    assert report.net_pnl == "98"

    assert reporting_date("2026-08-09T21:30:00+00:00", 180, "local", local_zone=timezone(timedelta(hours=7))).isoformat() == "2026-08-10"
