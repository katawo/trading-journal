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


def test_strategy_profile_persists_optional_backtest_context(tmp_path) -> None:
    repository = _repository(tmp_path)

    repository.save_strategy_profile(
        name="Motimoti",
        description="Trend-continuation setup after a pullback.",
        backtest_start_date="2024-01-01",
        backtest_end_date="2024-12-31",
        backtest_trade_count=120,
        backtest_win_rate="57.5",
        backtest_expectancy_r="0.42",
        backtest_net_r="50.4",
        backtest_notes="M15 XAUUSD sample, 1R fixed risk.",
    )

    profile = repository.get_strategy_profile("motimoti")

    assert profile is not None
    assert profile.name == "Motimoti"
    assert profile.backtest_period == "2024-01-01 to 2024-12-31"
    assert profile.backtest_trade_count == 120
    assert profile.backtest_win_rate == "57.5"
    assert profile.backtest_expectancy_r == "0.42"
    assert profile.backtest_net_r == "50.4"


def test_strategy_profile_validates_backtest_ranges(tmp_path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="end date"):
        repository.save_strategy_profile(
            name="Motimoti",
            description=None,
            backtest_start_date="2024-12-31",
            backtest_end_date="2024-01-01",
            backtest_trade_count=None,
            backtest_win_rate=None,
            backtest_expectancy_r=None,
            backtest_net_r=None,
            backtest_notes=None,
        )

    with pytest.raises(ValueError, match="between 0 and 100"):
        repository.save_strategy_profile(
            name="Motimoti",
            description=None,
            backtest_start_date=None,
            backtest_end_date=None,
            backtest_trade_count=10,
            backtest_win_rate="101",
            backtest_expectancy_r=None,
            backtest_net_r=None,
            backtest_notes=None,
        )


def test_dashboard_links_live_strategy_performance_to_backtest_context(tmp_path) -> None:
    repository = _repository(tmp_path)
    profile = repository.save_strategy_profile(
        name="Motimoti",
        description=None,
        backtest_start_date=None,
        backtest_end_date=None,
        backtest_trade_count=80,
        backtest_win_rate="55",
        backtest_expectancy_r="0.3",
        backtest_net_r="24",
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
    assert strategy.backtest_trade_count == 80
    assert strategy.backtest_win_rate == "55"
    assert strategy.backtest_expectancy_r == "0.3"
    assert strategy.backtest_net_r == "24"


def test_default_strategy_is_inherited_by_every_trade(tmp_path) -> None:
    repository = _repository(tmp_path)
    for name in ["Motimoti", "Reversal"]:
        repository.save_strategy_profile(
            name=name,
            description=None,
            backtest_start_date=None,
            backtest_end_date=None,
            backtest_trade_count=None,
            backtest_win_rate=None,
            backtest_expectancy_r=None,
            backtest_net_r=None,
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
    assert before["1001"].strategy_source == "Default"
    assert before["1002"].strategy == "Motimoti"
    assert before["1002"].strategy_source == "Default"

    repository.set_default_strategy("Reversal")
    after = {trade.position_id: trade for trade in repository.list_trades()}
    assert after["1001"].strategy == "Reversal"
    assert after["1002"].strategy == "Reversal"


def test_profile_rename_preserves_the_default_strategy_by_id(tmp_path) -> None:
    repository = _repository(tmp_path)
    profile = repository.save_strategy_profile(
        name="Motimoti",
        description=None,
        backtest_start_date=None,
        backtest_end_date=None,
        backtest_trade_count=None,
        backtest_win_rate=None,
        backtest_expectancy_r=None,
        backtest_net_r=None,
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
        backtest_start_date=None,
        backtest_end_date=None,
        backtest_trade_count=None,
        backtest_win_rate=None,
        backtest_expectancy_r=None,
        backtest_net_r=None,
        backtest_notes=None,
    )

    assert renamed.id == profile.id
    assert [item.name for item in repository.list_strategy_profiles()] == ["Journal default", "Motimoti Trend"]
    assert repository.get_journal_settings().default_strategy_name == "Motimoti Trend"
    assert repository.list_trades()[0].strategy == "Motimoti Trend"
