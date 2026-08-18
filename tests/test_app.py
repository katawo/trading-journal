from pathlib import Path
import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from trading_journal.application.display_time import format_relative_time
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def write_auto_export(path: Path) -> None:
    header = [
        "schema_version", "account_login", "broker_server", "account_currency", "position_id", "symbol", "direction",
        "entry_time", "exit_time", "server_utc_offset_minutes", "entry_price", "exit_price", "volume", "gross_pnl", "commission", "swap", "fees", "net_pnl",
        "entry_stop_price", "entry_target_price", "close_stop_price", "entry_magic_number", "entry_deal_count", "exit_reason", "initial_risk_amount", "initial_reward_amount", "account_balance", "pretrade_account_balance",
    ]
    row = {
        "schema_version": "5", "account_login": "123456", "broker_server": "DemoBroker-Live", "account_currency": "USD",
        "position_id": "9010", "symbol": "XAUUSD", "direction": "long", "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T09:00:00+00:00", "server_utc_offset_minutes": "0", "entry_price": "3300", "exit_price": "3310", "volume": "0.01",
        "gross_pnl": "20", "commission": "0", "swap": "0", "fees": "0", "net_pnl": "20",
        "entry_stop_price": "", "entry_target_price": "", "close_stop_price": "", "entry_magic_number": "", "entry_deal_count": "", "exit_reason": "client", "initial_risk_amount": "", "initial_reward_amount": "", "account_balance": "1000", "pretrade_account_balance": "",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerow(row)


def test_app_renders_local_mt5_import_entrypoint(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    # First AppTest run in the suite pays the one-time cold-start cost of
    # importing streamlit/pandas/plotly/etc.; the default ~3s timeout can be
    # too tight on a cold Windows CI runner.
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)

    assert not app.exception
    assert app.title[0].value == "Trade Compass"
    assert any("Local-first" in item.value for item in app.caption)

    import app as journal_app

    assert journal_app.application_version() == "0.1.12"
    assert journal_app.supported_mt5_schema_versions() == "5"


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


def test_desktop_recovery_can_request_a_reset_for_an_incompatible_database(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, base_currency VARCHAR(3) NOT NULL, reporting_timezone VARCHAR(64) NOT NULL, monthly_target VARCHAR NOT NULL)"
    )
    connection.commit()
    connection.close()
    data_directory = tmp_path / "desktop-data"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(data_directory))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert app.title[0].value == "Trade Compass recovery"
    assert any("predates the greenfield" in item.value for item in app.error)
    assert any(item.label == "Reset local database" for item in app.button)
    confirmation = next(item for item in app.text_input if item.label == "Type RESET to confirm")
    confirmation.set_value("RESET")
    next(item for item in app.button if item.label == "Reset local database").click().run()
    assert not (data_directory / "reset.request").exists()
    assert any("reload automatically" in item.value for item in app.info)

    import app as journal_app

    journal_app.request_desktop_database_reset()
    assert (data_directory / "reset.request").is_file()


def test_desktop_shows_a_diagnostic_for_a_corrupt_database(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    database_path.write_bytes(b"not a sqlite database")
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert app.title[0].value == "Trade Compass recovery"
    assert any(item.value == "Trade Compass could not open its local database." for item in app.error)
    assert not any(item.label == "Reset local database" for item in app.button)


def test_desktop_settings_can_request_a_database_reset(monkeypatch, tmp_path):
    data_directory = tmp_path / "desktop-data"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(data_directory))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    reset_button = next(item for item in app.button if item.label == "Reset local database")
    assert reset_button.disabled
    confirmation = next(item for item in app.text_input if item.label == "Type RESET to confirm")
    confirmation.set_value("RESET")
    next(item for item in app.button if item.label == "Reset local database").click().run()
    assert not (data_directory / "reset.request").exists()
    assert any("reload automatically" in item.value for item in app.info)


def test_format_relative_time_uses_compact_human_readable_durations():
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

    assert format_relative_time(now, now=now) == "just now"
    assert format_relative_time(now - timedelta(seconds=4), now=now) == "4s ago"
    assert format_relative_time(now - timedelta(minutes=1), now=now) == "1 min ago"
    assert format_relative_time(now - timedelta(hours=2), now=now) == "2 hr ago"
    assert format_relative_time(now - timedelta(days=3), now=now) == "3 days ago"


def test_currency_caption_escapes_markdown_math_delimiters():
    import app as journal_app

    assert journal_app.format_currency_caption("-30.63", "USD") == "−\\$30.63"
    assert journal_app.format_currency_caption("1000", "USD") == "+\\$1,000.00"


def test_global_framework_alert_bubble_combines_and_orders_cross_account_alerts(monkeypatch):
    import app as journal_app

    accounts = [
        SimpleNamespace(id=1, display_name="Zulu"),
        SimpleNamespace(id=2, display_name="Alpha"),
    ]
    alerts_by_account = {
        1: [SimpleNamespace(severity="warning", code="review_due", message="Review is due")],
        2: [SimpleNamespace(severity="critical", code="risk_stop", message="Daily risk stop reached")],
    }
    captured = {}

    class StubFrameworkService:
        def __init__(self, repo):
            pass

        def framework_alerts(self, account_id):
            return alerts_by_account[account_id]

    monkeypatch.setattr(journal_app, "FrameworkService", StubFrameworkService)
    monkeypatch.setattr(journal_app, "render_global_alert_bubble", lambda **kwargs: captured.update(kwargs))

    journal_app.render_global_framework_alert_bubble(SimpleNamespace(list_mt5_accounts=lambda: accounts))

    assert captured["label"] == "1 critical · 1 warning"
    assert captured["has_critical"] is True
    assert captured["alerts"] == [
        {"account_name": "Alpha", "code": "risk_stop", "message": "Daily risk stop reached", "severity": "critical"},
        {"account_name": "Zulu", "code": "review_due", "message": "Review is due", "severity": "warning"},
    ]


def test_framework_alert_codes_render_in_vietnamese_without_parsing_english(monkeypatch):
    from trading_journal.presentation import i18n

    monkeypatch.setattr(i18n, "language", lambda: "vi")

    assert i18n.framework_alert_message("psychology_developing", "Psychology is below 70 in the rolling sample.") == "Tâm lý thấp hơn 70 trong mẫu trượt."
    assert i18n.framework_alert_message("system_developing", "Trading system is below 70 in the rolling sample.") == "Hệ thống giao dịch thấp hơn 70 trong mẫu trượt."



def test_settings_groups_reporting_accounts_risk_strategies_and_review_rules(monkeypatch, tmp_path):
    common_files = tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))
    monkeypatch.setenv("TRADING_JOURNAL_MT5_COMMON_FILES", str(common_files))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    assert [tab.label for tab in app.tabs] == ["Account & risk", "Strategies", "Review context", "Review rules"]
    next(item for item in app.button if item.label == "New account").click().run()

    assert any(item.label == "Strategy name" for item in app.text_input)
    assert any(item.label == "Strategy description" for item in app.text_area)
    assert any(item.label == "Continue to MT5 account" for item in app.button)
    assert all(item.label != "Monthly target" for item in app.number_input)
    assert all(item.label != "Default risk (1R)" for item in app.number_input)
    assert all(item.label != "Apply a default planned-risk baseline to all trades" for item in app.checkbox)
    assert any("calendar used for reports and limits" in item.value for item in app.caption)
    assert any(item.label == "Save calendar" for item in app.button)


def test_workspace_navigation_switches_from_settings_back_to_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    assert any(item.value == "Settings" for item in app.subheader)
    assert any(item.label == "Strategy name" for item in app.text_input)

    app.switch_page("app_pages/dashboard.py").run()
    assert [item.value for item in app.subheader] == ["Performance dashboard"]
    assert not app.tabs
    assert not any(item.label == "Account name" for item in app.text_input)


def test_ongoing_is_first_in_navigation_while_dashboard_remains_default() -> None:
    source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")

    ongoing_page = 'st.Page("app_pages/ongoing.py", title=ongoing_title'
    dashboard_page = 'st.Page("app_pages/dashboard.py", title=tr("Dashboard"), icon=":material/dashboard:", default=True)'
    assert source.index(ongoing_page) < source.index(dashboard_page)
    assert 'ongoing_title = f"Ongoing ({ongoing_count})" if ongoing_count else "Ongoing"' in source


def test_ongoing_page_renders_its_auto_refreshing_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/ongoing.py").run()

    assert not app.exception
    assert any("Ongoing positions" in item.value for item in app.markdown)
    assert any("separate from closed-trade reporting" in item.value for item in app.caption)
    assert any("Add and select an MT5 account" in item.value for item in app.info)


def test_guidance_page_explains_the_post_trade_three_pillar_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/guidance.py").run()

    assert not app.exception
    guide = "\n".join(item.value for item in app.markdown)
    assert "Operating the three-pillar journal" in guide
    assert "Psychology, Risk management, and Trading system" in guide
    assert "post-trade and advisory" in guide
    assert "Worked example" in guide
    assert "94.17" in guide
    assert "single source of truth" in guide
    assert [item.label for item in app.selectbox] == ["Language"]


def test_account_creation_starts_with_a_required_trading_system_step(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    assert not any(item.label == "Continue to MT5 account" for item in app.button)
    assert any("No account selected" in item.value for item in app.info)

    next(item for item in app.button if item.label == "New account").click().run()

    assert any(item.label == "Strategy name" for item in app.text_input)
    assert any(item.label == "Continue to MT5 account" for item in app.button)


def test_onboarding_system_step_reflects_mode_after_going_back(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.save_strategy_profile(
        name="aaa", description="desc", backtest_notes=None,
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    next(item for item in app.button if item.label == "New account").click().run()

    next(item for item in app.segmented_control if item.label == "System source").set_value("Use saved system").run()
    next(item for item in app.button if item.label == "Continue to MT5 account").click().run()
    next(item for item in app.button if item.label == "Back").click().run()
    next(item for item in app.segmented_control if item.label == "System source").set_value("Create system").run()

    assert not any(item.label == "Trading system" for item in app.selectbox)
    assert any(item.label == "Strategy name" for item in app.text_input)


def test_new_account_resets_stale_onboarding_state(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    next(item for item in app.button if item.label == "New account").click().run()

    next(item for item in app.segmented_control if item.label == "System source").set_value("Create system").run()
    [item for item in app.text_input if item.label == "Strategy name"][-1].set_value("aaa").run()
    [item for item in app.text_area if item.label == "Strategy description"][-1].set_value("desc").run()
    next(item for item in app.button if item.label == "Continue to MT5 account").click().run()
    next(item for item in app.text_input if item.label == "Account name").set_value("acc").run()
    next(item for item in app.text_input if item.label == "Currency").set_value("EUR").run()
    next(item for item in app.text_input if item.label == "MT5 account ID").set_value("1111").run()
    next(item for item in app.text_input if item.label == "Broker server").set_value("asdf").run()
    next(item for item in app.text_input if item.label == "Funded capital").set_value("1000").run()
    next(item for item in app.button if item.label == "Continue to risk policy").click().run()
    next(item for item in app.text_input if item.label == "Standard risk (1R) %").set_value("").run()

    next(item for item in app.button if item.label == "New account").click().run()

    assert any(item.label == "Strategy name" for item in app.text_input)
    next(item for item in app.segmented_control if item.label == "System source").set_value("Create system").run()
    [item for item in app.text_input if item.label == "Strategy name"][-1].set_value("bbb").run()
    [item for item in app.text_area if item.label == "Strategy description"][-1].set_value("desc2").run()
    next(item for item in app.button if item.label == "Continue to MT5 account").click().run()

    assert next(item for item in app.text_input if item.label == "Currency").value == "USD"
    next(item for item in app.text_input if item.label == "Account name").set_value("acc2").run()
    next(item for item in app.text_input if item.label == "MT5 account ID").set_value("2222").run()
    next(item for item in app.text_input if item.label == "Broker server").set_value("fdsa").run()
    next(item for item in app.text_input if item.label == "Funded capital").set_value("2000").run()
    next(item for item in app.button if item.label == "Continue to risk policy").click().run()

    assert next(item for item in app.text_input if item.label == "Standard risk (1R) %").value == "2"


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
    next(item for item in app.text_input if item.label == "Funded capital").set_value("1000")
    next(item for item in app.button if item.label == "Update account").click().run()

    updated = SQLiteJournalRepository(database_path).list_mt5_accounts()[0]
    assert updated.display_name == "Renamed account"
    assert updated.funded_capital == "1000"
    assert any("MT5 account updated." in item.value for item in app.success)


def test_settings_can_change_an_unlocked_accounts_strategy(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    first = repository.save_strategy_profile(
        name="Motimoti", description="Trend-continuation setup.", backtest_notes=None,
    )
    second = repository.save_strategy_profile(
        name="Reversal", description="Fade extended moves.", backtest_notes=None,
    )
    repository.register_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD",
        export_file_path="", strategy_profile_id=first.id,
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    next(item for item in app.text_input if item.label == "Funded capital").set_value("1000").run()
    next(item for item in app.selectbox if item.label == "Trading system").set_value(second.id).run()
    next(item for item in app.button if item.label == "Update account").click().run()

    updated_repository = SQLiteJournalRepository(database_path)
    account = updated_repository.list_mt5_accounts()[0]
    assert updated_repository.get_account_strategy(account.id).name == "Reversal"


def test_settings_locks_strategy_selection_once_trades_are_imported(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Primary",
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
        "lock-test",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    assert not any(item.label == "Trading system" for item in app.selectbox)
    assert any("locked because trades have been imported" in item.value for item in app.caption)


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


def test_settings_can_delete_an_unused_mt5_account(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Unused account",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    next(item for item in app.checkbox if item.label == "I understand this permanently deletes this unused account").check().run()
    next(item for item in app.button if item.label == "Delete account").click().run()

    assert not app.exception
    assert SQLiteJournalRepository(database_path).list_mt5_accounts() == []
    assert any("MT5 account deleted." in item.value for item in app.success)


def test_settings_can_switch_the_active_mt5_account(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    strategy = repository.save_strategy_profile(
        name="Trend", description="Rules", backtest_notes=None,
    )
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
        strategy_profile_id=strategy.id,
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    assert not any(item.label == "Set as active account" for item in app.button)

    repository.register_mt5_account(
        display_name="Secondary", login="654321", broker_server="DemoBroker-Live", account_currency="USD",
        export_file_path="", strategy_profile_id=strategy.id,
    )
    secondary = repository.find_active_mt5_account("654321", "DemoBroker-Live")
    assert secondary is not None
    repository.set_active_mt5_account(secondary.id)

    assert not app.exception
    assert repository.get_active_mt5_account().display_name == "Secondary"


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
    app.switch_page("app_pages/bearings_review.py").run()

    assert not app.exception
    assert app.subheader[0].value == "Three-pillar framework"
    assert any("No completed MT5 positions" in item.value for item in app.info)
    assert not any(item.label == "Roadmap pillar" for item in app.segmented_control)


def test_improve_tab_shows_auto_detected_and_manual_roadmap_items(monkeypatch, tmp_path):
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
        opening_balance="1000",
    )
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        pretrade_balance_auto_evidence_enabled=False,
    )
    strategy = repository.save_strategy_profile(
        name="Trend continuation",
        description="Trade a confirmed pullback continuation.",
        backtest_verified=True,
        backtest_notes="Representative sample including modeled costs.",
    )
    repository.save_strategy_setup(
        strategy_profile_id=strategy.id,
        name="Standard pullback",
        description="Valid: pullback holds prior structure. Invalid: break of structure before entry.",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_improve.py").run()
    app.run()

    assert not app.exception
    assert any("Readiness roadmap" in item.value for item in app.markdown)
    markdown_text = "\n".join(item.value for item in app.markdown)
    # Risk: policy_and_sizing auto-detected complete from the saved policy, advancing past Define with no click.
    assert ":green[✓ Define]" in markdown_text
    assert "Risk 1%/trade, max 1%, daily 2R, weekly 4R." in markdown_text
    # An unrelated profile cannot advance this account's system roadmap.
    assert "**▶ Define**" in markdown_text
    assert "Backtest verified." not in markdown_text
    # Psychology has no structured equivalent, so both level-1 items stay manual.
    assert sum(1 for item in app.checkbox if item.label == "I completed this step") >= 2
    # Risk's still-manual "test" item also renders as a form, not auto-detected.
    assert any(item.label == "Evidence note" for item in app.text_area)
    completed_evidence = [item for item in app.status if item.label == "Completed evidence"]
    assert completed_evidence


def test_dashboard_uses_the_active_account_for_framework_status(monkeypatch, tmp_path):
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

    assert [item.label for item in app.selectbox] == ["Language"]
    assert repository.get_active_mt5_account().display_name == "Primary"
    assert any("Primary · 123456" in item.value for item in app.caption)


def test_language_selection_persists_and_loads_the_vietnamese_guide(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    language = next(item for item in app.selectbox if item.label == "Language")
    language.set_value("vi").run()

    assert SQLiteJournalRepository(database_path).get_journal_settings().display_language == "vi"
    assert app.title[0].value == "Trade Compass"
    app.switch_page("app_pages/guidance.py").run()
    guide = "\n".join(item.value for item in app.markdown)
    assert "Vận hành nhật ký giao dịch ba trụ cột" in guide


def test_review_tab_surfaces_the_last_saved_periods_priority_action(monkeypatch, tmp_path):
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
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            )
        ],
        "positions.csv",
        "focus-banner-test",
    )
    repository.save_framework_period_review(
        account_id=account.id,
        cadence="weekly",
        period_start="2026-08-03",
        period_end="2026-08-09",
        psychology_score="100",
        risk_score="100",
        system_score="100",
        readiness_score="100",
        alert_codes=(),
        recurring_issues=(),
        review_note="Clean week.",
        priority_action="Wait for the written setup before entering.",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()

    assert not app.exception
    assert any("Wait for the written setup before entering." in item.value for item in app.info)


def test_monitor_tab_shows_early_estimate_not_incomplete_for_a_partial_sample(monkeypatch, tmp_path):
    # A reviewed trade with no saved period review triggers a real "review due"
    # alert, which needs a live session for its bidi component — see the
    # identical note on the Caution-cap test below.
    monkeypatch.setattr("trading_journal.presentation.global_alert_bubble.render_global_alert_bubble", lambda **kwargs: None)
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
    strategy = repository.save_strategy_profile(
        name="Trend continuation",
        description=None,
        backtest_notes=None,
    )
    all_pass = {
        "rule_adherence": "pass", "impulse_control": "pass", "emotional_control": "pass", "patience_discipline": "pass",
        "policy_adherence": "pass", "position_size_accuracy": "pass", "stop_discipline": "pass", "exposure_limit_compliance": "pass",
        "setup_validity": "pass", "context_alignment": "pass", "entry_fidelity": "pass", "invalidation_fidelity": "pass", "management_exit_fidelity": "pass",
    }
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            )
        ],
        "positions.csv",
        "early-estimate-test",
    )
    trade = repository.list_closed_trades_for_review(account.id)[0]
    repository.save_post_trade_assessment(
        account_id=account.id,
        trade_id=trade.id,
        risk_policy_id=None,
        strategy_profile_id=strategy.id,
        criterion_grades=all_pass,
        violation_codes=(),
        hard_rule_codes=(),
        declared_actual_risk_amount=None,
        post_review_note="Reviewed independently of trade P&L.",
        corrective_action=None,
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_monitor.py").run()
    app.run()

    assert not app.exception
    psychology = next(m for m in app.metric if m.label == "Psychology")
    assert psychology.value == "100%"
    assert "Early estimate" in psychology.delta
    assert "Incomplete" not in psychology.delta
    assert any(item.label == "Overall readiness" for item in app.metric)
    captions = {item.value for item in app.caption}
    assert "Trader-wide" in captions
    assert "Account: Primary · 123456 · DemoBroker-Live" in captions
    assert "System: Primary · 123456 · DemoBroker-Live" in captions


def test_monitor_tab_explains_why_a_pillar_is_capped(monkeypatch, tmp_path):
    # This scenario deliberately scores a pillar below 70, which triggers the
    # cross-account alert bubble's real bidi-component render path — a path
    # AppTest's bare execution mode doesn't support outside of a live session.
    # This test is about the Monitor tab's own caption, not the alert bubble.
    monkeypatch.setattr("trading_journal.presentation.global_alert_bubble.render_global_alert_bubble", lambda **kwargs: None)
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
    strategy = repository.save_strategy_profile(
        name="Trend continuation",
        description=None,
        backtest_notes=None,
    )
    all_pass = {
        "rule_adherence": "pass", "impulse_control": "pass", "emotional_control": "pass", "patience_discipline": "pass",
        "policy_adherence": "pass", "position_size_accuracy": "pass", "stop_discipline": "pass", "exposure_limit_compliance": "pass",
        "setup_validity": "pass", "context_alignment": "pass", "entry_fidelity": "pass", "invalidation_fidelity": "pass", "management_exit_fidelity": "pass",
    }
    for index in range(2):
        repository.upsert_mt5_positions(
            account.id,
            [
                MT5PositionExport(
                    schema_version=5,
                    account_login="123456",
                    broker_server="DemoBroker-Live",
                    account_currency="USD",
                    position_id=f"cap-{index}",
                    symbol="XAUUSD",
                    direction="long",
                    entry_time=f"2026-08-{index + 3:02d}T08:00:00+00:00",
                    exit_time=f"2026-08-{index + 3:02d}T09:00:00+00:00",
                    entry_price="3300",
                    exit_price="3320",
                    volume="0.1",
                    gross_pnl="20",
                    commission="0",
                    swap="0",
                    fees="0",
                    net_pnl="20",
                )
            ],
            "positions.csv",
            f"caution-cap-test-{index}",
        )
        trade = next(item for item in repository.list_closed_trades_for_review(account.id) if item.position_id == f"cap-{index}")
        repository.save_post_trade_assessment(
            account_id=account.id,
            trade_id=trade.id,
            risk_policy_id=None,
            strategy_profile_id=strategy.id,
            criterion_grades=all_pass,
            violation_codes=("revenge",),
            hard_rule_codes=(),
            declared_actual_risk_amount=None,
            post_review_note="Reviewed independently of trade P&L.",
            corrective_action="Step away after a loss before re-entering.",
        )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_monitor.py").run()
    app.run()

    assert not app.exception
    assert any("cap this pillar at 59" in item.value for item in app.caption)


def test_register_flags_the_specific_hard_blocked_pillar(monkeypatch, tmp_path):
    # A hard-rule failure triggers a real cross-account alert, which needs a live
    # session for its bidi component — see the identical note above.
    monkeypatch.setattr("trading_journal.presentation.global_alert_bubble.render_global_alert_bubble", lambda **kwargs: None)
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
    strategy = repository.save_strategy_profile(
        name="Trend continuation",
        description=None,
        backtest_notes=None,
    )
    all_pass = {
        "rule_adherence": "pass", "impulse_control": "pass", "emotional_control": "pass", "patience_discipline": "pass",
        "policy_adherence": "pass", "position_size_accuracy": "pass", "stop_discipline": "pass", "exposure_limit_compliance": "pass",
        "setup_validity": "pass", "context_alignment": "pass", "entry_fidelity": "pass", "invalidation_fidelity": "pass", "management_exit_fidelity": "pass",
    }
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            )
        ],
        "positions.csv",
        "hard-block-flag-test",
    )
    trade = repository.list_closed_trades_for_review(account.id)[0]
    repository.save_post_trade_assessment(
        account_id=account.id,
        trade_id=trade.id,
        risk_policy_id=None,
        strategy_profile_id=strategy.id,
        criterion_grades=all_pass,
        violation_codes=("stop_widened",),
        hard_rule_codes=("stop_widened",),
        declared_actual_risk_amount=None,
        post_review_note="Reviewed independently of trade P&L.",
        corrective_action="Do not widen stops.",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    next(item for item in app.segmented_control if item.label == "Review status").set_value("all").run()

    assert not app.exception
    assert any("R 100% ⚠" in item.value for item in app.caption)
    assert any("This overrides the numeric score above" in item.value for item in app.caption)


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
        backtest_verified=True,
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
    app.switch_page("app_pages/bearings_review.py").run()

    assert not app.exception
    review_filter = next(item for item in app.segmented_control if item.label == "Review status")
    assert review_filter.value == "needs_approval"
    assert review_filter.options == ["Requires review (1)", "Auto-reviewed (0)", "Reviewed (0)", "All (1)"]
    assert any(item.label == "Show failed only" for item in app.checkbox)
    assert any(item.label.startswith("Select LT-") for item in app.checkbox)
    assert any(item.label == "Review" for item in app.button)
    assert not any(item.label == "Ungroup" for item in app.button)
    assert any("Automatic risk evidence only counts toward scores once approved" in item.value for item in app.caption)
    assert not any(item.label == "Closed MT5 position" for item in app.selectbox)
    assert not any(item.label == "Save review" for item in app.button)

    review_filter.set_value("manual_reviewed").run()

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
    assert next(item for item in app.segmented_control if item.label == "Review status").value == "manual_reviewed"
    assert any(item.label == "Review" for item in app.button)

    next(item for item in app.checkbox if item.label == "Show failed only").set_value(True).run()

    assert any("No reviewed (failed) trades" in item.value for item in app.info)


def test_approving_within_policy_evidence_moves_the_trade_from_auto_reviewed_to_reviewed(monkeypatch, tmp_path):
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
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        starting_balance="1000",
    )
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
                initial_risk_amount="5",
                entry_stop_price="3280",
            )
        ],
        "positions.csv",
        "auto-review-reclassify-test",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    next(item for item in app.segmented_control if item.label == "Review status").set_value("all").run()

    review_filter = next(item for item in app.segmented_control if item.label == "Review status")
    assert review_filter.options == ["Requires review (0)", "Auto-reviewed (1)", "Reviewed (0)", "All (1)"]

    next(item for item in app.button if item.label == "Approve").click().run()

    review_filter = next(item for item in app.segmented_control if item.label == "Review status")
    assert review_filter.options == ["Requires review (0)", "Auto-reviewed (0)", "Reviewed (1)", "All (1)"]
    active = repository.list_active_post_trade_assessments(account.id)[0]
    assert active.method == "auto"
    assert active.risk_policy_state == "within_policy"


def test_bulk_quick_reviewing_selected_trades_requires_confirmation(monkeypatch, tmp_path):
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
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        starting_balance="1000",
    )
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id=position_id,
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
                initial_risk_amount="5",
                entry_stop_price="3280",
            )
            for position_id in ("9010", "9011")
        ],
        "positions.csv",
        "bulk-approve-test",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    next(item for item in app.segmented_control if item.label == "Review status").set_value("auto_reviewed").run()

    review_filter = next(item for item in app.segmented_control if item.label == "Review status")
    assert review_filter.options == ["Requires review (0)", "Auto-reviewed (2)", "Reviewed (0)", "All (2)"]
    for checkbox in app.checkbox:
        if checkbox.label.startswith("Select LT-"):
            checkbox.set_value(True).run()
    next(item for item in app.button if item.label == "Quick review selected (2)").click().run()

    assert not app.exception
    assert any(item.label == "Quick review 2 selected" for item in app.button)
    next(item for item in app.button if item.label == "Quick review 2 selected").click().run()

    assert not app.exception
    active = repository.list_active_post_trade_assessments(account.id)
    assert len(active) == 2
    assert all(item.method == "auto" and item.risk_policy_state == "within_policy" for item in active)
    assert any("No auto-reviewed trades" in item.value for item in app.info)


def test_reopening_review_after_upgrading_an_auto_review_does_not_crash(monkeypatch, tmp_path):
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
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        starting_balance="1000",
    )
    repository.save_strategy_profile(
        name="Trend continuation",
        description=None,
        backtest_notes=None,
    )
    repository.upsert_mt5_positions(
        account.id,
        [
            MT5PositionExport(
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="9010",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
                initial_risk_amount="5",
                entry_stop_price="3280",
            )
        ],
        "positions.csv",
        "reopen-after-upgrade-test",
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    next(item for item in app.segmented_control if item.label == "Review status").set_value("all").run()
    next(item for item in app.button if item.label == "Approve").click().run()

    next(item for item in app.button if item.label == "Review").click().run()
    assert not app.exception
    next(item for item in app.button if item.label == "Mark all criteria as Pass").click().run()
    note = next(item for item in app.text_area if item.label == "What happened and what did you learn? *")
    note.set_value("Upgraded from an auto review.").run()
    next(item for item in app.button if item.label == "Save assessment").click().run()
    assert not app.exception

    next(item for item in app.button if item.label == "Review").click().run()

    assert not app.exception
    assert any("Assessment history" in item.label for item in app.expander)


def test_save_and_review_next_skips_a_stale_queue_entry_instead_of_dropping_the_queue(monkeypatch, tmp_path):
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
                schema_version=5,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id=position_id,
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-10T08:00:00+00:00",
                exit_time="2026-08-10T09:00:00+00:00",
                entry_price="3300",
                exit_price="3320",
                volume="0.1",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            )
            for position_id in ("stale-queue-1", "stale-queue-2")
        ],
        "positions.csv",
        "stale-queue-test",
    )
    real_trade_id = next(
        item.id for item in repository.list_closed_trades_for_review(account.id) if item.position_id == "stale-queue-2"
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    stale_trade_id = 999999
    app.session_state["post-trade-review-trade-id"] = stale_trade_id
    app.session_state["post-trade-review-queue"] = (real_trade_id,)
    app.run()

    assert not app.exception
    assert app.session_state["post-trade-review-trade-id"] == real_trade_id
    assert app.session_state["post-trade-review-queue"] == ()
    assert any(item.label == "What happened and what did you learn? *" for item in app.text_area)


def test_framework_groups_positions_through_a_confirmation_step(monkeypatch, tmp_path):
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
    positions = [
        MT5PositionExport(
            schema_version=1,
            account_login="123456",
            broker_server="DemoBroker-Live",
            account_currency="USD",
            position_id=f"group-{index}",
            symbol="XAUUSD",
            direction="long",
            entry_time="2026-08-10T08:00:00+00:00",
            exit_time=f"2026-08-10T09:{index:02d}:00+00:00",
            entry_price="3300",
            exit_price="3310",
            volume="0.01",
            gross_pnl="20",
            commission="0",
            swap="0",
            fees="0",
            net_pnl="20",
        )
        for index in range(26)
    ]
    repository.upsert_mt5_positions(account.id, positions, "positions.csv", "group-dialog")
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    assert len([item for item in app.checkbox if item.label.startswith("Select LT-")]) == 25
    next(item for item in app.checkbox if item.label.startswith("Select LT-")).set_value(True).run()
    next(item for item in app.button if item.label == "Next").click().run()

    assert any("Page 2 of 2" in item.value for item in app.caption)
    assert len([item for item in app.checkbox if item.label.startswith("Select LT-")]) == 1
    next(item for item in app.checkbox if item.label.startswith("Select LT-")).set_value(True).run()
    next(item for item in app.button if item.label == "Create logical trade (2)").click().run()

    assert not app.exception
    assert any("selected single-position logical trades" in item.value for item in app.caption)
    next(item for item in app.button if item.label == "Create logical trade").click().run()

    assert not app.exception
    assert any(item.label == "Confirm regroup" for item in app.button)
    next(item for item in app.button if item.label == "Confirm regroup").click().run()

    assert not app.exception
    grouped = repository.list_closed_trades_for_review(account.id)
    assert len(grouped) == 25
    assert any(item.position_count == 2 for item in grouped)
    next(item for item in app.button if item.label == "Ungroup").click().run()

    assert not app.exception
    assert any(item.label == "Confirm disband" for item in app.button)
    next(item for item in app.button if item.label == "Confirm disband").click().run()

    assert not app.exception
    assert len(repository.list_closed_trades_for_review(account.id)) == 26


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
    assert any(item.value == "Concentration (80/20)" for item in app.subheader)
    assert len(app.metric) >= 10
    assert any(item.label == "Sync MT5 now" for item in app.button)
    concentration_control = next(item for item in app.segmented_control if item.label == "Concentration view")
    assert any("No losing logical trades" in item.value for item in app.info)
    concentration_control.set_value("Trade").run()
    assert not app.exception
    assert {item.label for item in app.metric} >= {"Account balance", "Account drawdown", "Profit factor", "Worst day"}


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


def test_dashboard_surfaces_a_live_sync_failure_without_blocking_closed_trade_import(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    export_path = tmp_path / "positions.csv"
    write_auto_export(export_path)
    (tmp_path / "123456_open_positions.csv").write_text("not,a,valid,live,snapshot\n", encoding="utf-8")
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
    assert any("Primary live positions" in item.value for item in app.error)
    assert any("snapshot metadata row" in item.value for item in app.error)


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
    assert len(app.dataframe) == 1
    assert any("current logical-trade grouping" in item.value for item in app.caption)


def test_settings_strategies_tab_renders_optional_backtest_verification(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    # Cold Streamlit startup on the Windows CI runner can exceed AppTest's
    # three-second default before the settings page is exercised.
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.switch_page("app_pages/settings.py").run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Account & risk", "Strategies", "Review context", "Review rules"]
    assert any(item.value == "Strategy library" for item in app.subheader)
    assert any(item.label == "Backtest verified" for item in app.checkbox)


def test_saving_a_new_strategy_immediately_shows_up_in_the_list(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    strategy_name_field = [item for item in app.text_input if item.label == "Strategy name"][-1]
    strategy_name_field.set_value("Motimoti").run()
    strategy_description_field = [item for item in app.text_area if item.label == "Strategy description"][-1]
    strategy_description_field.set_value("Trend-continuation setup.").run()
    next(item for item in app.button if item.label == "Save strategy").click().run()

    assert not app.exception
    assert any("Motimoti" in item.value for item in app.markdown)
    assert any(item.label == "Strategy setups" for item in app.expander)


def test_strategy_setup_form_resets_after_each_add(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    [item for item in app.text_input if item.label == "Strategy name"][-1].set_value("Motimoti").run()
    [item for item in app.text_area if item.label == "Strategy description"][-1].set_value("Trend continuation").run()
    next(item for item in app.button if item.label == "Save strategy").click().run()

    next(item for item in app.text_input if item.label == "Setup name").set_value("London pullback").run()
    next(item for item in app.button if item.label == "Add setup").click().run()
    assert not app.exception
    assert next(item for item in app.text_input if item.label == "Setup name").value == ""

    next(item for item in app.text_input if item.label == "Setup name").set_value("NY breakout").run()
    next(item for item in app.button if item.label == "Add setup").click().run()
    assert not app.exception
    assert next(item for item in app.text_input if item.label == "Setup name").value == ""
    assert any("London pullback" in item.value for item in app.markdown)
    assert any("NY breakout" in item.value for item in app.markdown)


def test_strategy_setup_can_be_edited_and_deactivated(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    [item for item in app.text_input if item.label == "Strategy name"][-1].set_value("Motimoti").run()
    [item for item in app.text_area if item.label == "Strategy description"][-1].set_value("Trend continuation").run()
    next(item for item in app.button if item.label == "Save strategy").click().run()

    next(item for item in app.text_input if item.label == "Setup name").set_value("London pullback").run()
    next(item for item in app.button if item.label == "Add setup").click().run()

    next(item for item in app.button if item.label == "Edit" and "strategy-setup" in item.key).click().run()

    assert not app.exception
    assert next(item for item in app.text_input if item.label == "Setup name").value == "London pullback"
    assert any(item.label == "Update setup" for item in app.button)

    next(item for item in app.checkbox if item.label == "Active").set_value(False).run()
    next(item for item in app.button if item.label == "Update setup").click().run()

    assert not app.exception
    repository = SQLiteJournalRepository(database_path)
    profile = repository.get_strategy_profile("motimoti")
    assert repository.list_strategy_setups(profile.id, include_inactive=True)[0].active is False
