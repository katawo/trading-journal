from trading_journal.application.framework import FrameworkService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def test_account_framework_snapshot_loads_trade_history_once(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Primary",
        login="snapshot-once",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    account = repository.find_active_mt5_account("snapshot-once", "DemoBroker-Live")
    assert account is not None

    original = repository.list_closed_trades_for_review
    calls = 0

    def counted(account_id):
        nonlocal calls
        calls += 1
        return original(account_id)

    monkeypatch.setattr(repository, "list_closed_trades_for_review", counted)

    snapshot = FrameworkService(repository).account_snapshot(account.id)

    assert snapshot.account_id == account.id
    assert snapshot.review_queue_count == 0
    assert calls == 1


def test_account_framework_snapshots_never_mix_account_history(tmp_path):
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    for name, login in (("Primary", "account-a"), ("Secondary", "account-b")):
        repository.register_mt5_account(
            display_name=name,
            login=login,
            broker_server="DemoBroker-Live",
            account_currency="USD",
            export_file_path="",
        )
    first = repository.find_active_mt5_account("account-a", "DemoBroker-Live")
    second = repository.find_active_mt5_account("account-b", "DemoBroker-Live")
    assert first is not None and second is not None
    repository.upsert_mt5_positions(
        second.id,
        [
            MT5PositionExport(
                schema_version=1,
                account_login="account-b",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="second-only",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-09-03T08:00:00+00:00",
                exit_time="2026-09-03T09:00:00+00:00",
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
        "account-isolation-test",
    )

    first_snapshot = FrameworkService(repository).account_snapshot(first.id)
    second_snapshot = FrameworkService(repository).account_snapshot(second.id)

    assert first_snapshot.account_id == first.id
    assert first_snapshot.review_queue_count == 0
    assert second_snapshot.account_id == second.id
    assert second_snapshot.review_queue_count == 1
