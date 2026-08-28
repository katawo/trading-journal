from pathlib import Path
import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from streamlit.testing.v1 import AppTest

from trading_journal.application.display_time import format_relative_time
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import ASSESSMENT_CRITERIA, SQLiteJournalRepository
from trading_journal.presentation.framework import _format_trade_duration


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


def _review_filter_checkboxes(app):
    return {
        "needs_approval": next(item for item in app.checkbox if item.label.startswith("Requires review")),
        "auto_reviewed": next(item for item in app.checkbox if item.label.startswith("Auto-reviewed")),
        "manual_reviewed": next(item for item in app.checkbox if item.label.startswith("Reviewed (")),
    }


def _set_review_filters(app, *, needs_approval=None, auto_reviewed=None, manual_reviewed=None):
    checkboxes = _review_filter_checkboxes(app)
    if needs_approval is not None:
        checkboxes["needs_approval"].set_value(needs_approval)
    if auto_reviewed is not None:
        checkboxes["auto_reviewed"].set_value(auto_reviewed)
    if manual_reviewed is not None:
        checkboxes["manual_reviewed"].set_value(manual_reviewed)
    app.run()


def test_coaching_focus_keeps_editable_fields_inside_action_dialogs():
    script = """
from types import SimpleNamespace
from trading_journal.presentation.framework import _render_framework_focus


class Repo:
    def list_framework_focuses(self, account_id):
        return []


class Service:
    def ensure_coaching_focus(self, account_id):
        return None

    def focus_progress(self, account_id):
        focus = SimpleNamespace(
            id=41,
            pillar="psychology",
            metric_kind="component",
            action_text="Pause after a loss and re-check the setup.",
            coach_reason="Repeated reviewed issue.",
            hypothesis="",
            created_at="2026-08-20T08:00:00+00:00",
            baseline_value="55",
            target_value="70",
        )
        progress = SimpleNamespace(
            current_value="70",
            reviews_completed=5,
            target_reviews=5,
            ready_to_evaluate=True,
        )
        return focus, progress


_render_framework_focus(
    Repo(),
    SimpleNamespace(id=7),
    Service(),
    (),
    compact=True,
    show_heading=False,
)
"""
    app = AppTest.from_string(script).run()

    assert not app.exception
    assert not app.text_area
    assert not app.segmented_control
    assert {button.label for button in app.button} >= {"Edit action", "Resolve focus"}

    next(button for button in app.button if button.label == "Edit action").click()
    app.run()

    assert not app.exception
    assert [field.label for field in app.text_area] == ["Tailor the next-trade action"]
    assert not app.segmented_control

    resolution_app = AppTest.from_string(script).run()
    next(button for button in resolution_app.button if button.label == "Resolve focus").click()
    resolution_app.run()

    assert not resolution_app.exception
    assert [field.label for field in resolution_app.text_area] == ["Focus reflection"]
    assert [control.label for control in resolution_app.segmented_control] == ["Focus outcome"]


def test_trade_duration_uses_compact_review_table_units():
    assert _format_trade_duration("2026-08-10T08:00:00+00:00", "2026-08-10T08:00:30+00:00") == "<1m"
    assert _format_trade_duration("2026-08-10T08:00:00+00:00", "2026-08-10T09:35:00+00:00") == "1h 35m"
    assert _format_trade_duration("2026-08-10T08:00:00+00:00", "2026-08-12T11:15:00+00:00") == "2d 3h"


def test_app_renders_local_mt5_import_entrypoint(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    # First AppTest run in the suite pays the one-time cold-start cost of
    # importing streamlit/pandas/plotly/etc.; the default ~3s timeout can be
    # too tight on a cold Windows CI runner.
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)

    assert not app.exception
    assert app.title[0].value == "Trade Compass"
    from trading_journal.presentation import branding

    captured = {}
    monkeypatch.setattr(branding.st, "html", lambda value: captured.setdefault("value", value))
    branding.render_trade_doctrine("Survival · Consistency · Discipline")
    assert "trade-compass-doctrine" in captured["value"]
    assert "Survival" in captured["value"]

    import app as journal_app

    assert journal_app.application_version() == "0.1.12"
    assert journal_app.supported_mt5_schema_versions() == "5"


def test_database_change_token_includes_sqlite_wal_changes(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import app as journal_app

    database_path = tmp_path / "journal.db"
    wal_path = tmp_path / "journal.db-wal"
    database_path.write_bytes(b"database")
    wal_path.write_bytes(b"wal")
    database_path.touch()
    before = journal_app._database_change_token(database_path)

    wal_path.write_bytes(b"wal changed")
    after = journal_app._database_change_token(database_path)

    assert after != before
    assert after[:2] == before[:2]
    assert after[3] > before[3]


def test_review_save_immediately_invalidates_the_menu_badge_count(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import app as journal_app

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
                position_id="badge-review",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-19T08:00:00+00:00",
                exit_time="2026-08-19T09:00:00+00:00",
                entry_price="3300",
                exit_price="3310",
                volume="0.01",
                gross_pnl="10",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="10",
            )
        ],
        "positions.csv",
        "badge-review-test",
    )
    trade = repository.list_closed_trades_for_review(account.id)[0]
    before = journal_app._database_change_token(database_path)
    assert journal_app._cached_review_queue_count(str(database_path), before, account.id) == 1

    repository.save_post_trade_assessment(
        account_id=account.id,
        trade_id=trade.id,
        risk_policy_id=None,
        strategy_profile_id=repository.get_account_strategy(account.id).id,
        criterion_grades={criterion: "pass" for criterion in ASSESSMENT_CRITERIA},
        violation_codes=(),
        hard_rule_codes=(),
        declared_actual_risk_amount=None,
        post_review_note="Reviewed for menu badge invalidation.",
        corrective_action=None,
    )

    after = journal_app._database_change_token(database_path)
    assert after != before
    assert journal_app._cached_review_queue_count(str(database_path), after, account.id) == 0


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


@pytest.mark.desktop
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


@pytest.mark.desktop
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


@pytest.mark.desktop
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
    assert 'ongoing_title = f"{tr(\'Ongoing\')} ({ongoing_count})" if ongoing_count else tr("Ongoing")' in source


def test_ongoing_page_renders_its_auto_refreshing_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/ongoing.py").run()

    assert not app.exception
    assert any("Ongoing positions" in item.value for item in app.markdown)
    assert any("separate from closed-trade reporting" in item.value for item in app.caption)
    assert any("Add and select an MT5 account" in item.value for item in app.info)


def test_ongoing_page_does_not_claim_positions_are_flat_before_the_first_snapshot(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/ongoing.py").run()

    assert not app.exception
    assert any("Position data will appear after the first live MT5 snapshot" in item.value for item in app.caption)
    assert not any("No open positions in the latest live snapshot" in item.value for item in app.info)


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
    assert "latest 10 to 100 approved Auto and Manual reviews" in guide
    assert "Quick Risk Check" in guide
    assert "Deep Review" in guide
    assert "Analysis period" in guide
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


def test_settings_can_disable_an_imported_mt5_account(monkeypatch, tmp_path):
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
    next(item for item in app.button if item.label == "Disable account").click().run()

    assert not app.exception
    assert SQLiteJournalRepository(database_path).list_mt5_accounts() == []
    assert any("MT5 account disabled." in item.value for item in app.success)


def test_settings_can_reactivate_a_disabled_mt5_account(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Retained account",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    account = repository.list_mt5_accounts()[0]
    repository.register_mt5_account(
        display_name="Current account",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    current = next(item for item in repository.list_mt5_accounts() if item.display_name == "Current account")
    repository.set_active_mt5_account(current.id)
    repository.deactivate_mt5_account(account.id)
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    assert any("Retained account" in item.value for item in app.markdown)
    next(item for item in app.button if item.label == "Reactivate").click().run()

    reopened = SQLiteJournalRepository(database_path)
    restored = reopened.get_active_mt5_account()
    assert not app.exception
    assert restored is not None
    assert restored.id == current.id
    assert {item.id for item in reopened.list_mt5_accounts()} == {account.id, current.id}
    assert reopened.list_disabled_mt5_accounts() == []
    assert any("was reactivated" in item.value for item in app.success)


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
    repository.register_mt5_account(
        display_name="Secondary", login="654321", broker_server="DemoBroker-Live", account_currency="USD",
        export_file_path="", strategy_profile_id=strategy.id,
    )
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()
    quick_activate = next(item for item in app.button if item.label == "Set active")
    quick_activate.click().run()

    assert not app.exception
    assert repository.get_active_mt5_account().display_name == "Secondary"
    account_headings = [item.value for item in app.markdown if item.value in {"**Primary**", "**Secondary**"}]
    assert account_headings == ["**Secondary**", "**Primary**"]
    assert any(item.label == "Set as active account" for item in app.button)


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
    assert "từ 10 đến 100 đánh giá đã được phê duyệt" in guide
    assert "Đánh giá nhanh" in guide
    assert "Đánh giá chuyên sâu" in guide
    assert "Kỳ phân tích" in guide


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
                position_id="9009",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-07-14T08:00:00+00:00",
                exit_time="2026-07-14T09:00:00+00:00",
                entry_price="3250",
                exit_price="3260",
                volume="0.1",
                gross_pnl="10",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="10",
            ),
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
    for trade in repository.list_closed_trades_for_review(account.id):
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
    repository.save_framework_period_review(
        account_id=account.id,
        cadence="monthly",
        period_start="2026-06-01",
        period_end="2026-06-30",
        psychology_score="90",
        risk_score="80",
        system_score="70",
        readiness_score="70",
        alert_codes=("risk_stop",),
        recurring_issues=("stop_widened",),
        review_note="Historical review note.",
        priority_action="Keep the original stop.",
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
    assert "Psychology: Primary · 123456 · DemoBroker-Live" in captions
    assert "Account: Primary · 123456 · DemoBroker-Live" in captions
    assert "System: Primary · 123456 · DemoBroker-Live" in captions
    markdown = {item.value for item in app.markdown}
    assert any("Ongoing periods" in value for value in markdown)
    assert any("Latest completed periods" in value for value in markdown)
    assert any("Past periods requiring attention" in value for value in markdown)
    assert any("Review calendar: UTC." in item.value for item in app.info)
    assert any(value.startswith("Review opens ") for value in captions)
    assert any(item.label == "Choose a period" for item in app.selectbox)
    assert sum(item.label == "Save period review" for item in app.button) == 1
    history = next(item.value for item in app.dataframe if "Review note" in item.value.columns)
    assert history.iloc[0]["Cadence"] == "Monthly review"
    assert history.iloc[0]["Recurring issues"] == "Moved the stop loss farther away"
    assert history.iloc[0]["Alerts"] == "Risk stop"


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
    psychology = next(item for item in app.metric if item.label == "Psychology")
    assert psychology.value == "59%"
    assert "Caution" in psychology.delta
    assert "Raw 100%" in psychology.delta
    assert "2 in sample" in psychology.delta
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
    _set_review_filters(app, auto_reviewed=True, manual_reviewed=True)

    assert not app.exception
    assert any("R 100% ⚠" in item.value for item in app.caption)
    assert any("This overrides the numeric score above" in item.value for item in app.caption)


def test_framework_renders_a_filtered_review_register(monkeypatch, tmp_path):
    # Saving the assessment can create an alert after the badge cache is
    # invalidated; AppTest cannot mount the v2 browser component.
    monkeypatch.setattr("trading_journal.presentation.global_alert_bubble.render_global_alert_bubble", lambda **kwargs: None)
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
    review_filters = _review_filter_checkboxes(app)
    assert review_filters["needs_approval"].value is True
    assert review_filters["needs_approval"].label == "Requires review (1)"
    assert review_filters["auto_reviewed"].value is False
    assert review_filters["auto_reviewed"].label == "Auto-reviewed (0)"
    assert review_filters["manual_reviewed"].value is False
    assert review_filters["manual_reviewed"].label == "Reviewed (0)"
    assert not any(item.label == "Show failed only" for item in app.checkbox)
    assert any(item.label == "Check all" for item in app.checkbox)
    assert any(item.label.startswith("Select LT-") for item in app.checkbox)
    assert any(item.label == "Review" for item in app.button)
    assert not any(item.label == "Ungroup" for item in app.button)
    execution_labels = {item.value for item in app.caption}
    assert {"Opened", "Entry price", "Closed", "Exit price", "Duration", "Size"} <= execution_labels
    execution_values = {item.value for item in app.markdown}
    assert {"**3,300**", "**3,310**", "**1h**", "**0.01 lots**"} <= execution_values
    assert any("Automatic risk evidence only counts toward scores once approved" in item.value for item in app.caption)
    assert not any(item.label == "Closed MT5 position" for item in app.selectbox)
    assert not any(item.label == "Save review" for item in app.button)

    _set_review_filters(app, needs_approval=False, manual_reviewed=True)

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
    review_filters = _review_filter_checkboxes(app)
    assert review_filters["manual_reviewed"].value is True
    assert review_filters["needs_approval"].value is False
    assert any(item.label == "Review" for item in app.button)

    check_all = next(item for item in app.checkbox if item.label == "Check all")
    check_all.set_value(True).run()
    assert all(item.value for item in app.checkbox if item.label.startswith("Select LT-"))


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
    _set_review_filters(app, auto_reviewed=True, manual_reviewed=True)

    review_filters = _review_filter_checkboxes(app)
    assert review_filters["needs_approval"].label == "Requires review (0)"
    assert review_filters["auto_reviewed"].label == "Auto-reviewed (1)"
    assert review_filters["manual_reviewed"].label == "Reviewed (0)"

    next(item for item in app.button if item.label == "Approve").click().run()

    review_filters = _review_filter_checkboxes(app)
    assert review_filters["needs_approval"].label == "Requires review (0)"
    assert review_filters["auto_reviewed"].label == "Auto-reviewed (0)"
    assert review_filters["manual_reviewed"].label == "Reviewed (1)"
    active = repository.list_active_post_trade_assessments(account.id)[0]
    assert active.method == "auto"
    assert active.risk_policy_state == "within_policy"


def test_bulk_quick_reviewing_selected_trades_requires_confirmation(monkeypatch, tmp_path):
    # This flow can create an alert after the badge cache is invalidated. The
    # v2 browser component is not supported by AppTest's bare execution mode.
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
    _set_review_filters(app, needs_approval=False, auto_reviewed=True)

    review_filters = _review_filter_checkboxes(app)
    assert review_filters["needs_approval"].label == "Requires review (0)"
    assert review_filters["auto_reviewed"].label == "Auto-reviewed (2)"
    assert review_filters["manual_reviewed"].label == "Reviewed (0)"
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
    # This flow can create an alert after the badge cache is invalidated. The
    # v2 browser component is not supported by AppTest's bare execution mode.
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
    strategy = repository.get_account_strategy(account.id)
    repository.save_strategy_setup(strategy_profile_id=strategy.id, name="London pullback")
    repository.save_review_context_tag(kind="session", name="London")
    repository.save_review_context_tag(kind="regime", name="Trending")
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
    _set_review_filters(app, auto_reviewed=True, manual_reviewed=True)
    next(item for item in app.button if item.label == "Approve").click().run()

    next(item for item in app.button if item.label == "Review").click().run()
    assert not app.exception
    markdown_values = [item.value for item in app.markdown]
    lower_section_headings = {
        "##### Mistakes and rule breaches",
        "##### Reflection and action",
        "##### Risk evidence",
    }
    assert [value for value in markdown_values if value in lower_section_headings][-3:] == [
        "##### Mistakes and rule breaches",
        "##### Reflection and action",
        "##### Risk evidence",
    ]
    assert [
        item.label
        for item in app.multiselect
        if item.label in {"Trading mistakes", "Hard-rule events"}
    ] == ["Trading mistakes", "Hard-rule events"]
    assert any(item.label == "Actual risk amount (optional)" for item in app.text_input)
    context_selectboxes = {
        item.label: item
        for item in app.selectbox
        if item.label in {"Setup (optional)", "Session (optional)", "Market regime (optional)"}
    }
    assert all(item.value is None for item in context_selectboxes.values())
    assert all(item.format_func(None) == "" for item in context_selectboxes.values())
    context_selectboxes["Setup (optional)"].select("London pullback").run()
    context_selectboxes["Session (optional)"].select("London").run()
    context_selectboxes["Market regime (optional)"].select("Trending").run()
    next(item for item in app.button if item.label == "Mark all criteria as Pass").click().run()
    note = next(item for item in app.text_area if item.label == "What happened and what did you learn? *")
    note.set_value("Upgraded from an auto review.").run()
    next(item for item in app.button if item.label == "Save assessment").click().run()
    assert not app.exception

    next(item for item in app.button if item.label == "Review").click().run()

    assert not app.exception
    assert any("Assessment history" in item.label for item in app.expander)
    reopened_context = {
        item.label: item.value.name
        for item in app.selectbox
        if item.label in {"Setup (optional)", "Session (optional)", "Market regime (optional)"}
    }
    assert reopened_context == {
        "Setup (optional)": "London pullback",
        "Session (optional)": "London",
        "Market regime (optional)": "Trending",
    }


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

    check_all_app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    check_all_app.switch_page("app_pages/bearings_review.py").run()
    assert len([item for item in check_all_app.checkbox if item.label.startswith("Select LT-")]) == 25
    next(item for item in check_all_app.checkbox if item.label == "Check all").set_value(True).run()
    assert all(item.value for item in check_all_app.checkbox if item.label.startswith("Select LT-"))
    next(item for item in check_all_app.button if item.label == "Next").click().run()
    assert len([item for item in check_all_app.checkbox if item.label.startswith("Select LT-")]) == 1
    assert next(item for item in check_all_app.checkbox if item.label.startswith("Select LT-")).value is True

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.switch_page("app_pages/bearings_review.py").run()
    next(item for item in app.checkbox if item.label.startswith("Select LT-")).set_value(True).run()
    next(item for item in app.button if item.label == "Next").click().run()

    assert any("Page 2 of 2" in item.value for item in app.caption)
    assert len([item for item in app.checkbox if item.label.startswith("Select LT-")]) == 1
    next(item for item in app.checkbox if item.label.startswith("Select LT-")).set_value(True).run()
    next(item for item in app.button if item.label == "Group selected (2)").click().run()

    assert not app.exception
    assert any("selected logical trades" in item.value for item in app.caption)
    next(item for item in app.button if item.label == "Create new logical trade").click().run()

    assert not app.exception
    assert any(item.label == "Confirm & review" for item in app.button)
    next(item for item in app.button if item.label == "Confirm & review").click().run()

    assert not app.exception
    grouped = repository.list_closed_trades_for_review(account.id)
    assert len(grouped) == 25
    source_group = next(item for item in grouped if item.position_count == 2)
    standalone = next(item for item in grouped if not item.is_group)
    assert app.session_state["post-trade-review-trade-id"] == source_group.id
    assert any(item.label == "What happened and what did you learn? *" for item in app.text_area)
    app.session_state["post-trade-review-trade-id"] = None
    app.run()

    next(item for item in app.checkbox if item.label == f"Select LT-{source_group.id}").set_value(True).run()
    next(item for item in app.checkbox if item.label == f"Select LT-{standalone.id}").set_value(True).run()
    next(item for item in app.button if item.label == "Group selected (2)").click().run()

    assert not app.exception
    assert any("new logical-trade ID" in item.value for item in app.caption)
    next(item for item in app.text_input if item.label == "Trade label (optional)").set_value("Extended logical trade").run()
    next(item for item in app.button if item.label == "Create new logical trade").click().run()
    next(item for item in app.button if item.label == "Confirm & review").click().run()

    assert not app.exception
    extended_trades = repository.list_closed_trades_for_review(account.id)
    assert len(extended_trades) == 24
    extended = next(item for item in extended_trades if item.position_count == 3)
    assert extended.id not in {source_group.id, standalone.id}
    assert extended.display_label == "Extended logical trade"
    assert app.session_state["post-trade-review-trade-id"] == extended.id
    app.session_state["post-trade-review-trade-id"] = None
    app.run()
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
    stat_markup = "\n".join(item.value for item in app.markdown)
    assert stat_markup.count("dashboard-stat-label") >= 10
    assert any(item.label == "Sync MT5 now" for item in app.button)
    concentration_control = next(item for item in app.segmented_control if item.label == "Concentration view")
    assert concentration_control.options == ["Trade", "Symbol"]
    assert concentration_control.value == "Trade"
    assert any("No losing logical trades" in item.value for item in app.info)
    assert len(app.get("plotly_chart")) >= 7
    statistics_breakdown = next(item for item in app.segmented_control if item.label == "Breakdown view")
    assert statistics_breakdown.options == ["Direction", "Symbol"]
    assert statistics_breakdown.value == "Direction"
    statistics_breakdown.set_value("Symbol").run()
    assert not app.exception
    concentration_control = next(item for item in app.segmented_control if item.label == "Concentration view")
    concentration_control.set_value("Symbol").run()
    assert not app.exception
    stat_markup = "\n".join(item.value for item in app.markdown)
    assert all(
        f">{label}<" in stat_markup for label in ("Account balance", "Account drawdown", "Profit factor", "Worst day")
    )


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
    breakdown = next(item.value for item in app.dataframe if "Group" in item.value.columns)
    assert breakdown["Win rate"].dtype.kind in "fi"
    assert breakdown["Net P&L (USD)"].dtype.kind in "fi"
    assert breakdown["Total R"].dtype.kind in "fi"
    assert breakdown["Expectancy R"].dtype.kind in "fi"
    assert breakdown["Profit factor"].dtype.kind in "fi"
    assert any("Logical trade" in item.value.columns for item in app.dataframe)
    section_headers = {item.value for item in app.markdown}
    assert {"**Performance**", "**Consistency**", "**Breakdowns**"}.issubset(section_headers)
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
