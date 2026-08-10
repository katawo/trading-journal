from __future__ import annotations

import csv
from pathlib import Path

import pytest

from trading_journal.application.import_mt5 import MT5ImportService
from trading_journal.domain.errors import ImportValidationError
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


HEADER = [
    "schema_version",
    "account_login",
    "broker_server",
    "account_currency",
    "position_id",
    "symbol",
    "direction",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "volume",
    "gross_pnl",
    "commission",
    "swap",
    "fees",
    "net_pnl",
]


def write_export(
    path: Path,
    *,
    currency: str = "USD",
    net_pnl: str = "98.00",
    schema_version: str = "1",
) -> None:
    row = {
        "schema_version": schema_version,
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": currency,
        "position_id": "9001",
        "symbol": "EURUSD",
        "direction": "long",
        "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T10:00:00+00:00",
        "entry_price": "1.10000",
        "exit_price": "1.10100",
        "volume": "1.00",
        "gross_pnl": "100.00",
        "commission": "-1.50",
        "swap": "-0.25",
        "fees": "-0.25",
        "net_pnl": net_pnl,
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerow(row)


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC", monthly_target="1000")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    return repository


def test_imports_closed_position_and_waits_for_planned_risk(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path)

    result = MT5ImportService(repository).import_csv(export_path)

    trade = repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001")
    assert result.created_count == 1
    assert result.updated_count == 0
    assert trade is not None
    assert trade.net_pnl == "98.00"
    assert trade.result_r is None
    assert trade.strategy is None


def test_reimport_refreshes_execution_data(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, net_pnl="98.00")
    service = MT5ImportService(repository)
    service.import_csv(export_path)
    write_export(export_path, net_pnl="102.00")

    result = service.import_csv(export_path)
    trade = repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001")

    assert result.created_count == 0
    assert result.updated_count == 1
    assert trade is not None
    assert trade.net_pnl == "102.00"
    assert trade.strategy is None
    assert trade.result_r is None


def test_rejects_currency_mismatch_without_creating_trade(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, currency="EUR")

    with pytest.raises(ImportValidationError, match="currency"):
        MT5ImportService(repository).import_csv(export_path)

    assert repository.count_trades() == 0


def test_rejects_an_unsupported_schema_version_without_creating_trade(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, schema_version="2")

    with pytest.raises(ImportValidationError, match="Unsupported MT5 export schema version; expected 1"):
        MT5ImportService(repository).import_csv(export_path)

    assert repository.count_trades() == 0
