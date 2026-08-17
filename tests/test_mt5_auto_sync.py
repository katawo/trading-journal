from __future__ import annotations

import csv
from pathlib import Path

from trading_journal.application.auto_sync import MT5AutoSyncService
from trading_journal.application.import_mt5 import MT5ImportService
from trading_journal.application.mt5_paths import default_mt5_export_path, find_mt5_common_files, mt5_export_filename
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
    "server_utc_offset_minutes",
    "entry_price",
    "exit_price",
    "volume",
    "gross_pnl",
    "commission",
    "swap",
    "fees",
    "net_pnl",
    "entry_stop_price",
    "entry_target_price",
    "close_stop_price",
    "entry_magic_number",
    "entry_deal_count",
    "exit_reason",
    "initial_risk_amount",
    "initial_reward_amount",
    "account_balance",
    "pretrade_account_balance",
]


def write_export(path: Path, *, net_pnl: str = "98.00") -> None:
    row = {
        "schema_version": "5",
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": "USD",
        "position_id": "9001",
        "symbol": "EURUSD",
        "direction": "long",
        "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T10:00:00+00:00",
        "server_utc_offset_minutes": "0",
        "entry_price": "1.10000",
        "exit_price": "1.10100",
        "volume": "1.00",
        "gross_pnl": "100.00",
        "commission": "-1.50",
        "swap": "-0.25",
        "fees": "-0.25",
        "net_pnl": net_pnl,
        "entry_stop_price": "",
        "entry_target_price": "",
        "close_stop_price": "",
        "entry_magic_number": "",
        "entry_deal_count": "",
        "exit_reason": "client",
        "initial_risk_amount": "",
        "initial_reward_amount": "",
        "account_balance": "1000.00",
        "pretrade_account_balance": "",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerow(row)


def configured_repository(tmp_path: Path, export_path: Path) -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path=str(export_path),
    )
    return repository


def test_default_export_path_uses_the_configured_mt5_common_files_root(monkeypatch, tmp_path: Path) -> None:
    common_files = tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"
    monkeypatch.setenv("TRADING_JOURNAL_MT5_COMMON_FILES", str(common_files))

    assert default_mt5_export_path() == str(common_files / "trading_journal" / "positions.csv")


def test_common_files_environment_override_wins_even_before_mt5_creates_the_directory(monkeypatch, tmp_path: Path) -> None:
    common_files = tmp_path / "configured" / "Common" / "Files"
    monkeypatch.setenv("TRADING_JOURNAL_MT5_COMMON_FILES", str(common_files))

    location = find_mt5_common_files()

    assert location.path == common_files
    assert location.source == "Environment override"


def test_common_files_lookup_supports_native_windows_appdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MT5_COMMON_FILES", raising=False)
    appdata = tmp_path / "AppData" / "Roaming"
    common_files = appdata / "MetaQuotes" / "Terminal" / "Common" / "Files"
    common_files.mkdir(parents=True)

    location = find_mt5_common_files(home=tmp_path, environment={"APPDATA": str(appdata)})

    assert location.path == common_files
    assert location.source == "Windows APPDATA"


def test_common_files_lookup_supports_windows_user_profile_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MT5_COMMON_FILES", raising=False)
    common_files = tmp_path / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
    common_files.mkdir(parents=True)

    location = find_mt5_common_files(home=tmp_path, environment={})

    assert location.path == common_files
    assert location.source == "Windows user profile"


def test_common_files_lookup_supports_wineprefix_and_non_matching_windows_user(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MT5_COMMON_FILES", raising=False)
    wine_prefix = tmp_path / "mt5-wine"
    common_files = wine_prefix / "drive_c" / "users" / "mt5-user" / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
    common_files.mkdir(parents=True)

    location = find_mt5_common_files(home=tmp_path, environment={"WINEPREFIX": str(wine_prefix)})

    assert location.path == common_files
    assert location.source == "Linux WINEPREFIX"


def test_common_files_lookup_prefers_an_existing_account_export_over_other_installations(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MT5_COMMON_FILES", raising=False)
    mt5_common_files = tmp_path / ".mt5" / "drive_c" / "users" / tmp_path.name / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
    mt5_common_files.mkdir(parents=True)
    wine_common_files = tmp_path / ".wine" / "drive_c" / "users" / tmp_path.name / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
    export = wine_common_files / "trading_journal" / "123456_positions.csv"
    export.parent.mkdir(parents=True)
    export.write_text("schema_version\n", encoding="utf-8")

    location = find_mt5_common_files("123456", home=tmp_path, environment={})

    assert location.path == wine_common_files
    assert location.source == "Linux Wine (~/.wine)"


def test_common_files_lookup_is_unresolved_when_no_supported_location_exists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MT5_COMMON_FILES", raising=False)

    location = find_mt5_common_files(home=tmp_path, environment={})

    assert location.path is None
    assert location.source == "Not detected"
    assert default_mt5_export_path("123456", home=tmp_path) == "trading_journal/123456_positions.csv"


def test_account_specific_export_path_uses_the_mt5_login(monkeypatch, tmp_path: Path) -> None:
    common_files = tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"
    monkeypatch.setenv("TRADING_JOURNAL_MT5_COMMON_FILES", str(common_files))

    assert mt5_export_filename("412074694") == "412074694_positions.csv"
    assert default_mt5_export_path("412074694") == str(common_files / "trading_journal" / "412074694_positions.csv")


def test_default_export_path_prefers_the_detected_common_files_export(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MT5_COMMON_FILES", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    mt5_common_files = tmp_path / ".mt5" / "drive_c" / "users" / tmp_path.name / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
    mt5_common_files.mkdir(parents=True)
    wine_export = tmp_path / ".wine" / "drive_c" / "users" / tmp_path.name / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / "trading_journal" / "positions.csv"
    wine_export.parent.mkdir(parents=True)
    wine_export.write_text("schema_version\\n", encoding="utf-8")

    assert default_mt5_export_path(home=tmp_path) == str(wine_export)


def test_auto_sync_imports_a_changed_registered_export_only_once(tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    repository = configured_repository(tmp_path, export_path)
    service = MT5AutoSyncService(repository)

    first = service.sync_configured_exports()
    second = service.sync_configured_exports()

    assert [(item.status, item.created_count, item.updated_count) for item in first] == [("imported", 1, 0)]
    assert [(item.status, item.created_count, item.updated_count) for item in second] == [("up_to_date", 0, 0)]
    assert first[0].export_updated_at is not None
    assert second[0].export_updated_at == first[0].export_updated_at
    assert repository.count_trades() == 1


def test_auto_sync_prefers_an_account_specific_export_beside_a_legacy_path(tmp_path: Path) -> None:
    legacy_path = tmp_path / "positions.csv"
    account_specific_path = tmp_path / "123456_positions.csv"
    write_export(account_specific_path)
    repository = configured_repository(tmp_path, legacy_path)

    results = MT5AutoSyncService(repository).sync_configured_exports()

    assert [(item.status, item.source_path, item.created_count) for item in results] == [("imported", str(account_specific_path), 1)]
    assert repository.count_trades() == 1


def test_auto_sync_records_a_validation_failure_without_changing_trades(tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    export_path.write_text("not,a,valid,export\n", encoding="utf-8")
    repository = configured_repository(tmp_path, export_path)

    results = MT5AutoSyncService(repository).sync_configured_exports()

    assert [(item.status, item.created_count, item.updated_count) for item in results] == [("failed", 0, 0)]
    assert repository.count_trades() == 0
    assert "contains no completed positions" in (results[0].message or "")


def test_auto_sync_waits_for_the_first_export_file(tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    repository = configured_repository(tmp_path, export_path)

    results = MT5AutoSyncService(repository).sync_configured_exports()

    assert [(item.status, item.created_count, item.updated_count) for item in results] == [("waiting", 0, 0)]
    assert repository.count_trades() == 0
    assert results[0].message == "Waiting for the first MT5 export."


def ingestion_configured_repository(tmp_path: Path) -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    return repository


def ingestion_row(position_id: str = "9001") -> dict:
    return {
        "schema_version": 5,
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": "USD",
        "position_id": position_id,
        "symbol": "EURUSD",
        "direction": "long",
        "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T10:00:00+00:00",
        "server_utc_offset_minutes": 0,
        "entry_price": "1.10000",
        "exit_price": "1.10100",
        "volume": "1.00",
        "gross_pnl": "100.00",
        "commission": "-1.50",
        "swap": "-0.25",
        "fees": "-0.25",
        "net_pnl": "98.00",
        "entry_stop_price": "1.09500",
        "entry_target_price": "1.11000",
        "close_stop_price": "1.09800",
        "entry_magic_number": "10001",
        "entry_deal_count": 1,
        "exit_reason": "take_profit",
        "initial_risk_amount": "50.00",
        "initial_reward_amount": "200.00",
        "account_balance": "1000.00",
        "pretrade_account_balance": "900.00",
    }


def test_auto_sync_reports_unconfigured_for_a_blank_export_path_outside_multiuser_mode(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression guard: a desktop/single-user-web account that simply hasn't configured
    a local export path yet must keep showing "unconfigured", not be mistaken for an
    ingestion-fed account just because TRADING_JOURNAL_MULTIUSER_MODE happens to be unset.
    """
    monkeypatch.delenv("TRADING_JOURNAL_MULTIUSER_MODE", raising=False)
    repository = ingestion_configured_repository(tmp_path)

    results = MT5AutoSyncService(repository).sync_configured_exports()

    assert [item.status for item in results] == ["unconfigured"]


def test_auto_sync_waits_for_the_first_ingestion_push_in_multiuser_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_MODE", "1")
    repository = ingestion_configured_repository(tmp_path)

    results = MT5AutoSyncService(repository).sync_configured_exports()

    assert [(item.status, item.message) for item in results] == [("waiting", "Waiting for the first MT5 export.")]
    assert repository.count_trades() == 0


def test_auto_sync_reports_up_to_date_after_a_successful_ingestion_push(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_MODE", "1")
    repository = ingestion_configured_repository(tmp_path)
    MT5ImportService(repository).import_json_positions([ingestion_row()], source_label="http:alice")

    results = MT5AutoSyncService(repository).sync_configured_exports()

    assert [item.status for item in results] == ["up_to_date"]
    assert results[0].export_updated_at is not None


def test_latest_ingestion_import_ignores_local_file_runs_and_returns_the_newest(tmp_path: Path) -> None:
    repository = ingestion_configured_repository(tmp_path)
    account = repository.list_mt5_accounts()[0]

    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    MT5ImportService(repository).import_csv(export_path)
    assert repository.latest_ingestion_import(account.id) is None

    MT5ImportService(repository).import_json_positions([ingestion_row("9101")], source_label="http:alice")
    first = repository.latest_ingestion_import(account.id)
    assert first is not None
    assert (first[1], first[2]) == (1, 0)

    MT5ImportService(repository).import_json_positions([ingestion_row("9101")], source_label="http:alice")
    second = repository.latest_ingestion_import(account.id)
    assert second is not None
    assert (second[1], second[2]) == (0, 1)


def test_resident_mt5_export_ea_is_event_driven_and_has_no_trading_operations() -> None:
    source = (Path(__file__).parents[1] / "mql5" / "TradingJournalSync.mq5").read_text(encoding="utf-8")

    assert 'input string CommonFilesSubfolder = "trading_journal";' in source
    assert "string ExportFileName()" in source
    assert "AccountInfoInteger(ACCOUNT_LOGIN)" in source
    assert '"_positions.csv"' in source
    assert "int OnInit()" in source
    assert "void OnTradeTransaction(" in source
    assert "TRADE_TRANSACTION_DEAL_ADD" in source
    assert "EventSetTimer" in source
    assert "FILE_COMMON" in source
    assert "FileMove" in source
    assert "OrderCalcProfit" in source
    assert "DEAL_SL" in source
    assert "DEAL_TP" in source
    assert '"initial_risk_amount"' in source
    assert '"entry_magic_number"' in source
    assert '"account_balance"' in source
    assert '"pretrade_account_balance"' in source
    assert "AccountInfoDouble(ACCOUNT_BALANCE)" in source
    assert "PreTradeBalance" in source
    for forbidden_operation in ("OrderSend(", "OrderDelete(", "PositionClose(", "PositionModify(", "CTrade"):
        assert forbidden_operation not in source


def test_mt5_exporters_format_prices_with_each_exported_symbols_precision() -> None:
    exporters = ("TradingJournalSync.mq5", "TradingJournalExporter.mq5")

    for exporter in exporters:
        source = (Path(__file__).parents[1] / "mql5" / exporter).read_text(encoding="utf-8")

        assert "int SymbolDigits(const string symbol)" in source
        assert "SymbolInfoInteger(symbol,SYMBOL_DIGITS,digits)" in source
        assert "int symbol_digits=SymbolDigits(symbol);" in source
        assert "OptionalNumber(entry_stop,symbol_digits)" in source
        assert "OptionalNumber(entry_target,symbol_digits)" in source
        assert "OptionalNumber(close_stop,symbol_digits)" in source
