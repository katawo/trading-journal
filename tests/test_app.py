from pathlib import Path
import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from trading_journal.application.display_time import format_relative_time
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def write_auto_export(path: Path) -> None:
    header = [
        "schema_version", "account_login", "broker_server", "account_currency", "position_id", "symbol", "direction",
        "entry_time", "exit_time", "server_utc_offset_minutes", "entry_price", "exit_price", "volume", "gross_pnl", "commission", "swap", "fees", "net_pnl",
        "entry_stop_price", "entry_target_price", "close_stop_price", "entry_magic_number", "entry_deal_count", "exit_reason", "initial_risk_amount", "initial_reward_amount", "account_balance",
    ]
    row = {
        "schema_version": "4", "account_login": "123456", "broker_server": "DemoBroker-Live", "account_currency": "USD",
        "position_id": "9010", "symbol": "XAUUSD", "direction": "long", "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T09:00:00+00:00", "server_utc_offset_minutes": "0", "entry_price": "3300", "exit_price": "3310", "volume": "0.01",
        "gross_pnl": "20", "commission": "0", "swap": "0", "fees": "0", "net_pnl": "20",
        "entry_stop_price": "", "entry_target_price": "", "close_stop_price": "", "entry_magic_number": "", "entry_deal_count": "", "exit_reason": "client", "initial_risk_amount": "", "initial_reward_amount": "", "account_balance": "1000",
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


def test_app_requires_a_reset_for_legacy_monthly_target_data(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, base_currency VARCHAR(3) NOT NULL, reporting_timezone VARCHAR(64) NOT NULL, monthly_target VARCHAR NOT NULL)"
    )
    connection.execute("INSERT INTO journal_settings VALUES (1, 'USD', 'UTC', '1000')")
    connection.commit()
    connection.close()
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert any("make reset-db CONFIRM_RESET=yes" in item.value for item in app.error)
    assert any(item.value == "make reset-db CONFIRM_RESET=yes" for item in app.code)


def test_format_relative_time_uses_compact_human_readable_durations():
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

    assert format_relative_time(now, now=now) == "just now"
    assert format_relative_time(now - timedelta(seconds=4), now=now) == "4s ago"
    assert format_relative_time(now - timedelta(minutes=1), now=now) == "1 min ago"
    assert format_relative_time(now - timedelta(hours=2), now=now) == "2 hr ago"
    assert format_relative_time(now - timedelta(days=3), now=now) == "3 days ago"


def test_settings_groups_reporting_with_accounts_and_strategies(monkeypatch, tmp_path):
    common_files = tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))
    monkeypatch.setenv("TRADING_JOURNAL_MT5_COMMON_FILES", str(common_files))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    assert [tab.label for tab in app.tabs] == ["MT5 Accounts", "Strategies"]
    assert any(item.label == "Account name" for item in app.text_input)
    assert any(item.label == "MT5 account ID" for item in app.text_input)
    funded_capital = next(item for item in app.text_input if item.label == "Funded capital (optional)")
    assert funded_capital.proto.placeholder == "Set now or later"
    assert app.expander[0].label == "Advanced: custom export location"
    assert any(item.label == "Add account" for item in app.button)
    assert all(item.label != "Monthly target" for item in app.number_input)
    assert all(item.label != "Default risk (1R)" for item in app.number_input)
    assert all(item.label != "Apply a default planned-risk baseline to all trades" for item in app.checkbox)
    assert any("Framework → Risk policy" in item.value for item in app.caption)
    assert any(item.label == "Save reporting settings" for item in app.button)
    export_path = next(item for item in app.text_input if item.label == "Custom export path (optional)")
    assert export_path.value == ""
    assert export_path.proto.placeholder == str(common_files / "trading_journal" / "<MT5-login>_positions.csv")
    assert any("Detected Common Files (Environment override)" in item.value for item in app.caption)


def test_workspace_navigation_switches_from_settings_back_to_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    assert any(item.value == "Settings" for item in app.subheader)
    assert any(item.label == "Account name" for item in app.text_input)

    app.switch_page("app_pages/dashboard.py").run()
    assert [item.value for item in app.subheader] == ["Performance dashboard"]
    assert not app.tabs
    assert not any(item.label == "Account name" for item in app.text_input)


def test_account_can_be_saved_before_its_balance_baseline_is_known(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    next(item for item in app.text_input if item.label == "MT5 account ID").set_value("123456")
    next(item for item in app.text_input if item.label == "Broker server").set_value("DemoBroker-Live")
    next(item for item in app.button if item.label == "Add account").click().run()

    assert not app.error
    assert any("MT5 account added." in item.value for item in app.success)
    repository = SQLiteJournalRepository(database_path)
    assert repository.list_mt5_accounts()[0].opening_balance is None


def test_settings_can_update_an_existing_mt5_account(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Original account",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="/tmp/original.csv",
    )
    account = repository.list_mt5_accounts()[0]
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    assert next(item for item in app.text_input if item.label == "Account name").value == "Original account"
    next(item for item in app.text_input if item.label == "Account name").set_value("Renamed account")
    next(item for item in app.text_input if item.label == "Funded capital (optional)").set_value("1000")
    next(item for item in app.button if item.label == "Update account").click().run()

    updated = SQLiteJournalRepository(database_path).list_mt5_accounts()[0]
    assert updated.display_name == "Renamed account"
    assert updated.funded_capital == "1000"
    assert any("MT5 account updated." in item.value for item in app.success)


def test_settings_can_deactivate_an_imported_mt5_account(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Obsolete account",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    account = repository.list_mt5_accounts()[0]
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=1,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
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
        "deactivate-test",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    next(item for item in app.button if item.label == "Deactivate account").click().run()

    assert not app.exception
    assert SQLiteJournalRepository(database_path).list_mt5_accounts() == []
    assert any("MT5 account deactivated." in item.value for item in app.success)


def test_framework_workspace_renders_account_scoped_post_trade_journal(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/framework.py").run()

    assert not app.exception
    assert app.subheader[0].value == "Three-pillar framework"
    assert [tab.label for tab in app.tabs] == ["Review trades", "Monitor", "Roadmap", "Risk policy", "Framework rules"]
    assert any("No completed MT5 positions" in item.value for item in app.info)
    assert any(item.label == "Roadmap pillar" for item in app.segmented_control)


def test_dashboard_uses_its_report_account_for_framework_status(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    for name, login in (("Primary", "123456"), ("Secondary", "654321")):
        repository.register_mt5_account(
            display_name=name,
            login=login,
            broker_server="DemoBroker-Live",
            account_currency="USD",
            export_file_path="",
        )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert [item.label for item in app.selectbox] == ["Report account"]
    assert all(item.label != "Framework account" for item in app.selectbox)
    assert any("Primary · 123456" in item.value for item in app.caption)


def test_framework_renders_a_filtered_review_register(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(reporting_time_basis="local")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.save_account_risk_policy(
        account_id=account.id,
        starting_balance="1000",
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
    repository.save_strategy_profile(
        name="Trend continuation",
        description="Confirmed pullback continuation.",
        backtest_start_date="2024-01-01",
        backtest_end_date="2025-01-01",
        backtest_trade_count=120,
        backtest_win_rate="52",
        backtest_expectancy_r="0.2",
        backtest_net_r="24",
        backtest_notes=None,
    )
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=1,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
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
        "assessment-test",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/framework.py").run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Review trades", "Monitor", "Roadmap", "Risk policy", "Framework rules"]
    assert any(item.label == "Review status" and item.value == "Needs review" for item in app.segmented_control)
    assert any(item.label == "Needs review" and item.value == "1" for item in app.metric)
    assert any(item.label == "Automatic risk evidence" and item.value == "0" for item in app.metric)
    assert any(item.label == "Reviewed" and item.value == "0" for item in app.metric)
    assert len(app.dataframe) >= 1
    assert not list(app.dataframe[0].proto.selection_mode)
    assert any("Automatic risk evidence is advisory" in item.value for item in app.caption)
    assert not any(item.label == "Closed MT5 position" for item in app.selectbox)
    assert not any(item.label == "Save review" for item in app.button)

    next(item for item in app.segmented_control if item.label == "Review status").set_value("Reviewed").run()

    assert any("No reviewed trades" in item.value for item in app.info)

    trade = repository.list_closed_trades_for_review(account.id)[0]
    policy = repository.get_active_risk_policy(account.id)
    strategy = repository.list_strategy_profiles()[0]
    assert policy is not None
    repository.save_post_trade_assessment(
        account_id=account.id,
        trade_id=trade.id,
        risk_policy_id=policy.id,
        strategy_profile_id=strategy.id,
        criterion_grades={
            "rule_adherence": "pass", "impulse_control": "pass", "emotional_control": "pass", "patience_discipline": "pass",
            "policy_adherence": "pass", "position_size_accuracy": "pass", "stop_discipline": "pass", "exposure_limit_compliance": "pass",
            "setup_validity": "pass", "context_alignment": "pass", "entry_fidelity": "pass", "invalidation_fidelity": "pass", "management_exit_fidelity": "pass",
        },
        violation_codes=(),
        hard_rule_codes=(),
        declared_actual_risk_amount="5",
        post_review_note="Followed the documented process.",
        corrective_action=None,
    )
    app.run()

    assert not app.exception
    assert any(item.label == "Reviewed" and item.value == "1" for item in app.metric)
    assert len(app.dataframe) >= 1


def test_dashboard_sync_keeps_the_current_mt5_export_imported(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    export_path = tmp_path / "positions.csv"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
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
    repository.configure_journal(reporting_time_basis="utc")
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
    assert len(app.metric) >= 10
    assert any(item.label == "Sync MT5 now" for item in app.button)
    assert {item.label for item in app.metric} >= {"Balance", "Max drawdown", "Profit factor", "Worst day"}


def test_dashboard_auto_imports_a_changed_configured_mt5_export(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    export_path = tmp_path / "positions.csv"
    write_auto_export(export_path)
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
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
    repository.configure_journal(reporting_time_basis="utc")
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
    repository.configure_journal(reporting_time_basis="utc")
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
    chart_view = next(item for item in app.segmented_control if item.label == "Chart view")
    assert chart_view.value == "Daily"

    chart_view.set_value("Per trade").run()

    assert not app.exception
    assert len(app.dataframe) == 2
    assert any("Drawdown is measured after each trade closes" in item.value for item in app.caption)


def test_settings_strategies_tab_renders_optional_backtest_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["MT5 Accounts", "Strategies"]
    assert any(item.value == "Strategy library" for item in app.subheader)
    assert any(item.label == "Backtest sample size" for item in app.text_input)
    assert any(item.label == "MT5 magic numbers (optional)" for item in app.text_input)
