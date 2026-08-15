from __future__ import annotations

import pytest

from trading_journal.application.dashboard import DashboardService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def _repository(tmp_path) -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    return repository


def test_new_journal_starts_with_a_default_strategy(tmp_path) -> None:
    repository = _repository(tmp_path)

    settings = repository.get_journal_settings()
    profiles = repository.list_strategy_profiles()

    assert [profile.name for profile in profiles] == ["Journal default"]
    assert settings.default_strategy_profile_id == profiles[0].id
    assert settings.default_strategy_name == "Journal default"


def test_strategy_profile_persists_backtest_verification(tmp_path) -> None:
    repository = _repository(tmp_path)

    repository.save_strategy_profile(
        name="Motimoti",
        description="Trend-continuation setup after a pullback.",
        backtest_verified=True,
        backtest_notes="M15 XAUUSD sample, 1R fixed risk.",
    )

    profile = repository.get_strategy_profile("motimoti")

    assert profile is not None
    assert profile.name == "Motimoti"
    assert profile.backtest_verified is True


def test_configured_account_creation_is_atomic_and_activates_the_account(tmp_path) -> None:
    repository = _repository(tmp_path)

    account = repository.create_configured_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD",
        export_file_path="", funded_capital="10000", strategy_profile_id=None,
        strategy_name="London continuation", strategy_description="Trade documented continuation rules.",
        standard_risk_per_trade_percent="1", maximum_risk_per_trade_percent="1", daily_loss_limit_r="2",
        weekly_loss_limit_r="4", max_drawdown_percent="10", max_open_risk_r="1", max_consecutive_losses=3,
        minimum_rr="1.5", correlation_policy=None,
    )

    assert repository.get_active_mt5_account() is not None
    assert repository.get_active_mt5_account().id == account.id
    assert account.funded_capital == "10000"
    assert account.strategy_name == "London continuation"
    assert repository.get_account_strategy(account.id).name == "London continuation"
    assert repository.list_strategy_setups(account.strategy_profile_id) == []
    assert repository.get_active_risk_policy(account.id) is not None


def test_delete_strategy_profile_removes_an_unbound_strategy(tmp_path) -> None:
    repository = _repository(tmp_path)
    profile = repository.save_strategy_profile(
        name="Unused", description="Not bound to anything.", backtest_notes=None,
    )
    repository.save_strategy_setup(strategy_profile_id=profile.id, name="London pullback")

    repository.delete_strategy_profile(profile.id)

    assert repository.get_strategy_profile("unused") is None


def test_delete_strategy_profile_rejects_a_strategy_bound_to_an_account(tmp_path) -> None:
    repository = _repository(tmp_path)
    profile = repository.save_strategy_profile(
        name="Bound", description="desc", backtest_notes=None,
    )
    repository.register_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD",
        export_file_path="", strategy_profile_id=profile.id,
    )

    with pytest.raises(ValueError, match="bound to an account"):
        repository.delete_strategy_profile(profile.id)

    assert repository.get_strategy_profile("bound") is not None


def test_configured_account_creation_rolls_back_on_invalid_risk_policy(tmp_path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="Maximum risk"):
        repository.create_configured_mt5_account(
            display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD",
            export_file_path="", funded_capital="10000", strategy_profile_id=None,
            strategy_name="London continuation", strategy_description="Rules.",
            standard_risk_per_trade_percent="2", maximum_risk_per_trade_percent="1", daily_loss_limit_r="2",
            weekly_loss_limit_r="4", max_drawdown_percent="10", max_open_risk_r="1", max_consecutive_losses=3,
            minimum_rr="1.5", correlation_policy=None,
        )

    assert repository.list_mt5_accounts() == []
    assert [profile.name for profile in repository.list_strategy_profiles()] == ["Journal default"]


def test_update_mt5_account_can_change_the_bound_strategy_before_trades_import(tmp_path) -> None:
    repository = _repository(tmp_path)
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
    account = repository.list_mt5_accounts()[0]

    repository.update_mt5_account(
        account_id=account.id, display_name=account.display_name, login=account.login, broker_server=account.broker_server,
        account_currency=account.account_currency, export_file_path=account.export_file_path, opening_balance=None,
        strategy_profile_id=second.id,
    )

    assert repository.get_account_strategy(account.id).name == "Reversal"


def test_update_mt5_account_locks_the_bound_strategy_once_trades_are_imported(tmp_path) -> None:
    repository = _repository(tmp_path)
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
    account = repository.list_mt5_accounts()[0]
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
                entry_time="2026-08-01T08:00:00+00:00",
                exit_time="2026-08-01T09:00:00+00:00",
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

    with pytest.raises(ValueError, match="locked once trades are imported"):
        repository.update_mt5_account(
            account_id=account.id, display_name=account.display_name, login=account.login, broker_server=account.broker_server,
            account_currency=account.account_currency, export_file_path=account.export_file_path, opening_balance=None,
            strategy_profile_id=second.id,
        )

    assert repository.get_account_strategy(account.id).name == "Motimoti"


def test_dashboard_links_live_strategy_performance_to_backtest_context(tmp_path) -> None:
    repository = _repository(tmp_path)
    profile = repository.save_strategy_profile(
        name="Motimoti",
        description=None,
        backtest_verified=True,
        backtest_notes=None,
    )
    repository.set_default_strategy(profile.id)
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
                entry_time="2026-08-01T08:00:00+00:00",
                exit_time="2026-08-01T09:00:00+00:00",
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
    strategy = DashboardService(repository).build_report(start_date="2026-08-01", end_date="2026-08-31").by_strategy[0]

    assert strategy.strategy == "Motimoti"
    assert strategy.net_pnl == "20"
    assert strategy.backtest_verified is True


def test_default_strategy_is_inherited_by_every_trade(tmp_path) -> None:
    repository = _repository(tmp_path)
    for name in ["Motimoti", "Reversal"]:
        repository.save_strategy_profile(
            name=name,
            description=None,
            backtest_notes=None,
        )
    repository.set_default_strategy("Motimoti")
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
                entry_time="2026-08-01T08:00:00+00:00",
                exit_time="2026-08-01T09:00:00+00:00",
                entry_price="3300",
                exit_price="3310",
                volume="0.01",
                gross_pnl="20",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="20",
            ),
            MT5PositionExport(
                schema_version=1,
                account_login="123456",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="1002",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-08-02T08:00:00+00:00",
                exit_time="2026-08-02T09:00:00+00:00",
                entry_price="3300",
                exit_price="3310",
                volume="0.01",
                gross_pnl="-5",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="-5",
            ),
        ],
        "positions.csv",
        "test-hash",
    )
    before = {trade.position_id: trade for trade in repository.list_trades()}
    assert before["1001"].strategy == "Motimoti"
    assert before["1001"].strategy_source == "Account"
    assert before["1002"].strategy == "Motimoti"
    assert before["1002"].strategy_source == "Account"

    repository.set_default_strategy("Reversal")
    after = {trade.position_id: trade for trade in repository.list_trades()}
    assert after["1001"].strategy == "Motimoti"
    assert after["1002"].strategy == "Motimoti"


def test_profile_rename_preserves_the_default_strategy_by_id(tmp_path) -> None:
    repository = _repository(tmp_path)
    profile = repository.save_strategy_profile(
        name="Motimoti",
        description=None,
        backtest_notes=None,
    )
    repository.set_default_strategy(profile.id)
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
                entry_time="2026-08-01T08:00:00+00:00",
                exit_time="2026-08-01T09:00:00+00:00",
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
    renamed = repository.save_strategy_profile(
        strategy_id=profile.id,
        name="Motimoti Trend",
        description=None,
        backtest_notes=None,
    )

    assert renamed.id == profile.id
    assert [item.name for item in repository.list_strategy_profiles()] == ["Journal default", "Motimoti Trend"]
    assert repository.get_journal_settings().default_strategy_name == "Motimoti Trend"
    assert repository.list_trades()[0].strategy == "Motimoti Trend"
