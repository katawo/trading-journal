from pathlib import Path
import csv
from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from trading_journal.application.display_time import format_relative_time
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def write_auto_export(path: Path) -> None:
    header = [
        "schema_version", "account_login", "broker_server", "account_currency", "position_id", "symbol", "direction",
        "entry_time", "exit_time", "entry_price", "exit_price", "volume", "gross_pnl", "commission", "swap", "fees", "net_pnl",
    ]
    row = {
        "schema_version": "1", "account_login": "123456", "broker_server": "DemoBroker-Live", "account_currency": "USD",
        "position_id": "9010", "symbol": "XAUUSD", "direction": "long", "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T09:00:00+00:00", "entry_price": "3300", "exit_price": "3310", "volume": "0.01",
        "gross_pnl": "20", "commission": "0", "swap": "0", "fees": "0", "net_pnl": "20",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerow(row)


def test_app_renders_local_mt5_import_entrypoint(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert app.title[0].value == "Trading Journal"
    assert any("Local-only" in item.value for item in app.caption)


def test_format_relative_time_uses_compact_human_readable_durations():
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

    assert format_relative_time(now, now=now) == "just now"
    assert format_relative_time(now - timedelta(seconds=4), now=now) == "4s ago"
    assert format_relative_time(now - timedelta(minutes=1), now=now) == "1 min ago"
    assert format_relative_time(now - timedelta(hours=2), now=now) == "2 hr ago"
    assert format_relative_time(now - timedelta(days=3), now=now) == "3 days ago"


def test_settings_uses_tabs_for_journal_accounts_and_strategies(monkeypatch, tmp_path):
    common_files = tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))
    monkeypatch.setenv("TRADING_JOURNAL_MT5_COMMON_FILES", str(common_files))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.sidebar.radio[0].set_value("Settings").run()

    assert [tab.label for tab in app.tabs] == ["General", "MT5 Accounts", "Strategies"]
    assert all(option not in app.sidebar.radio[0].options for option in ["Journal", "Strategies", "MT5 Import"])
    assert any(item.label == "Account name" for item in app.text_input)
    assert any(item.label == "MT5 account ID" for item in app.text_input)
    assert app.expander[0].label == "Advanced: custom export location"
    assert any(item.label == "Save account" for item in app.button)
    export_path = next(item for item in app.text_input if item.label == "Custom export path (optional)")
    assert export_path.value == ""
    assert export_path.proto.placeholder == str(common_files / "trading_journal" / "<MT5-login>_positions.csv")
    assert any("Detected Common Files (Environment override)" in item.value for item in app.caption)


def test_dashboard_sync_keeps_the_current_mt5_export_imported(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    export_path = tmp_path / "positions.csv"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC", monthly_target="100")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path=str(export_path),
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    write_auto_export(export_path)
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    assert not app.exception
    assert all(item.label != "Review trades" for item in app.button)
    sync_button = next(item for item in app.button if item.label == "Sync MT5 now")
    sync_button.click().run()

    assert not app.exception
    assert repository.count_trades() == 1
    assert any(
        "Auto-imported 1 created" in item.value
        or "Manual sync imported 1" in item.value
        or "already up to date" in item.value
        for item in app.success
    )
    assert any("Last update:" in item.value for item in app.caption)


def test_dashboard_renders_graphics_for_imported_trades(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(
        base_currency="USD",
        reporting_timezone="UTC",
        monthly_target="100",
        default_planned_risk_amount="10",
    )
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
            MT5PositionExport(
                schema_version=1,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="1001",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3310",
                volume="0.01",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            )
        ],
        "positions.csv",
        "test-hash",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert app.subheader[0].value == "Performance dashboard"
    assert len(app.metric) == 10
    assert any(item.label == "Sync MT5 now" for item in app.button)
    assert {item.label for item in app.metric} >= {"Balance", "Max drawdown", "Profit factor", "Worst day"}


def test_dashboard_auto_imports_a_changed_configured_mt5_export(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    export_path = tmp_path / "positions.csv"
    write_auto_export(export_path)
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC", monthly_target="100")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path=str(export_path),
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert repository.count_trades() == 1
    assert any("Auto-imported 1 created" in item.value for item in app.success)


def test_dashboard_surfaces_an_automatic_sync_failure(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    export_path = tmp_path / "positions.csv"
    export_path.write_text("not,a,valid,export\n", encoding="utf-8")
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(base_currency="USD", reporting_timezone="UTC", monthly_target="100")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path=str(export_path),
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert any("MT5 auto-sync needs attention: Primary:" in item.value for item in app.error)
    assert any("contains no completed positions" in item.value for item in app.error)


def test_dashboard_switches_to_per_trade_view(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(
        base_currency="USD",
        reporting_timezone="UTC",
        monthly_target="100",
        default_planned_risk_amount="10",
    )
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
            MT5PositionExport(
                schema_version=1,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="1001",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3310",
                volume="0.01",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            )
        ],
        "positions.csv",
        "test-hash",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    chart_view = next(item for item in app.radio if item.label == "Chart view")
    assert chart_view.value == "Daily"

    chart_view.set_value("Per trade").run()

    assert not app.exception
    assert len(app.dataframe) == 2
    assert any("Drawdown is measured after each trade closes" in item.value for item in app.caption)


def test_settings_strategies_tab_renders_optional_backtest_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.sidebar.radio[0].set_value("Settings").run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["General", "MT5 Accounts", "Strategies"]
    assert any(item.value == "Strategy library" for item in app.subheader)
    assert any(item.label == "Backtest sample size" for item in app.text_input)
