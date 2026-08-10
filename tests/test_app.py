from pathlib import Path

from streamlit.testing.v1 import AppTest

from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def test_app_renders_local_mt5_import_entrypoint(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert app.title[0].value == "Trading Journal"
    assert any("Local-only" in item.value for item in app.caption)


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
    assert {item.label for item in app.metric} >= {"Balance", "Max drawdown", "Profit factor", "Worst day"}


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


def test_strategy_workspace_renders_optional_backtest_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.sidebar.radio[0].set_value("Strategies").run()

    assert not app.exception
    assert app.subheader[0].value == "Strategy library"
    assert any(item.label == "Backtest sample size" for item in app.text_input)
