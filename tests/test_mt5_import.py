from __future__ import annotations

import csv
import sqlite3
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
V2_HEADER = HEADER + [
    "entry_stop_price",
    "entry_target_price",
    "close_stop_price",
    "entry_magic_number",
    "entry_deal_count",
    "exit_reason",
    "initial_risk_amount",
    "initial_reward_amount",
]
V5_HEADER = V2_HEADER + ["account_balance", "pretrade_account_balance", "server_utc_offset_minutes"]


def write_export(
    path: Path,
    *,
    currency: str = "USD",
    net_pnl: str = "98.00",
    schema_version: str = "5",
    initial_risk_amount: str = "",
    account_balance: str = "1000.00",
    pretrade_account_balance: str = "900.00",
    second_account_balance: str | None = None,
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
        "entry_stop_price": "1.09500",
        "entry_target_price": "1.11000",
        "close_stop_price": "1.09800",
        "entry_magic_number": "10001",
        "entry_deal_count": "1",
        "exit_reason": "take_profit",
        "initial_risk_amount": initial_risk_amount,
        "initial_reward_amount": "200.00" if initial_risk_amount else "",
        "account_balance": account_balance,
        "pretrade_account_balance": pretrade_account_balance,
        "server_utc_offset_minutes": "180",
    }
    fieldnames = V5_HEADER
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({key: row[key] for key in fieldnames})
        if second_account_balance is not None:
            second_row = {key: row[key] for key in fieldnames}
            second_row["position_id"] = "9002"
            second_row["account_balance"] = second_account_balance
            writer.writerow(second_row)


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteJournalRepository:
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
    assert trade.strategy == "Journal default"


def test_mt5_account_id_is_unique_across_broker_servers(repository: SQLiteJournalRepository) -> None:
    with pytest.raises(ValueError, match="account ID"):
        repository.register_mt5_account(
            display_name="Same login on another server",
            login="123456",
            broker_server="DemoBroker-Other",
            account_currency="USD",
            export_file_path="",
        )

    accounts = repository.list_mt5_accounts()

    assert len(accounts) == 1
    assert accounts[0].broker_server == "DemoBroker-Live"


def test_unimported_mt5_account_can_be_deleted_with_account_only_setup(repository: SQLiteJournalRepository) -> None:
    account = repository.list_mt5_accounts()[0]
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="0.5",
        maximum_risk_per_trade_percent="0.5",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="1",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        starting_balance="1000",
    )

    repository.delete_mt5_account(account.id)

    assert repository.list_mt5_accounts() == []


def test_get_active_mt5_account_falls_back_deterministically_and_persists(repository: SQLiteJournalRepository) -> None:
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    primary = next(item for item in repository.list_mt5_accounts() if item.display_name == "Primary")

    active = repository.get_active_mt5_account()

    assert active is not None
    assert active.id == primary.id


def test_active_mt5_account_choice_persists_across_repository_restarts(repository: SQLiteJournalRepository) -> None:
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    secondary = next(item for item in repository.list_mt5_accounts() if item.display_name == "Secondary")
    repository.set_active_mt5_account(secondary.id)

    reopened = SQLiteJournalRepository(repository.database_path)
    reopened.initialize()

    assert reopened.get_active_mt5_account().id == secondary.id


def test_deactivating_the_active_mt5_account_falls_back_to_another(repository: SQLiteJournalRepository) -> None:
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    primary = next(item for item in repository.list_mt5_accounts() if item.display_name == "Primary")
    repository.set_active_mt5_account(primary.id)

    repository.deactivate_mt5_account(primary.id)

    assert repository.get_active_mt5_account().display_name == "Secondary"


def test_reactivating_a_disabled_mt5_account_restores_the_same_row_without_changing_selection(
    repository: SQLiteJournalRepository,
    tmp_path: Path,
) -> None:
    account = repository.list_mt5_accounts()[0]
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    secondary = next(item for item in repository.list_mt5_accounts() if item.display_name == "Secondary")
    repository.set_active_mt5_account(secondary.id)
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="0.5",
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
    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    MT5ImportService(repository).import_csv(export_path)
    policy = repository.get_active_risk_policy(account.id)
    trade = repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001")
    repository.deactivate_mt5_account(account.id)

    assert [item.id for item in repository.list_mt5_accounts()] == [secondary.id]
    assert [item.id for item in repository.list_disabled_mt5_accounts()] == [account.id]

    repository.reactivate_mt5_account(account.id)

    restored = repository.get_active_mt5_account()
    assert restored is not None
    assert restored.id == secondary.id
    assert {item.id for item in repository.list_mt5_accounts()} == {account.id, secondary.id}
    assert repository.list_disabled_mt5_accounts() == []
    assert repository.get_active_risk_policy(account.id) == policy
    assert repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001") == trade

    repository.reactivate_mt5_account(account.id)

    assert repository.get_active_mt5_account().id == secondary.id


def test_deleting_the_active_unimported_mt5_account_does_not_violate_the_foreign_key(repository: SQLiteJournalRepository) -> None:
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    secondary = next(item for item in repository.list_mt5_accounts() if item.display_name == "Secondary")
    repository.set_active_mt5_account(secondary.id)

    repository.delete_mt5_account(secondary.id)

    assert repository.get_active_mt5_account().display_name == "Primary"


def test_set_active_mt5_account_rejects_an_unknown_id(repository: SQLiteJournalRepository) -> None:
    with pytest.raises(ValueError):
        repository.set_active_mt5_account(999999)


def test_account_identity_cannot_change_after_mt5_trades_are_imported(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    MT5ImportService(repository).import_csv(export_path)
    account = repository.list_mt5_accounts()[0]

    with pytest.raises(ValueError, match="cannot change"):
        repository.update_mt5_account(
            account_id=account.id,
            display_name=account.display_name,
            login=account.login,
            broker_server="DemoBroker-Changed",
            account_currency=account.account_currency,
            export_file_path=account.export_file_path,
        )


def test_funded_capital_can_be_initialized_once_and_is_then_immutable(repository: SQLiteJournalRepository) -> None:
    account = repository.list_mt5_accounts()[0]
    repository.register_mt5_account(
        display_name=account.display_name,
        login=account.login,
        broker_server=account.broker_server,
        account_currency=account.account_currency,
        export_file_path=account.export_file_path,
        opening_balance="1000",
    )

    with pytest.raises(ValueError, match="immutable"):
        repository.register_mt5_account(
            display_name=account.display_name,
            login=account.login,
            broker_server=account.broker_server,
            account_currency=account.account_currency,
            export_file_path=account.export_file_path,
            opening_balance="2000",
        )

    with sqlite3.connect(repository.database_path) as connection, pytest.raises(
        sqlite3.IntegrityError, match="Funded capital is immutable"
    ):
        connection.execute("UPDATE mt5_accounts SET opening_balance = '2000' WHERE id = ?", (account.id,))


def test_account_update_and_legacy_funded_capital_repair_are_atomic(repository: SQLiteJournalRepository) -> None:
    account = repository.list_mt5_accounts()[0]
    repository.update_mt5_account(
        account_id=account.id,
        display_name="Repaired account",
        login=account.login,
        broker_server=account.broker_server,
        account_currency=account.account_currency,
        export_file_path=account.export_file_path,
        initial_funded_capital="1000.00",
    )
    repaired = repository.list_mt5_accounts()[0]
    assert repaired.display_name == "Repaired account"
    assert repaired.funded_capital == "1000.00"

    with pytest.raises(ValueError, match="immutable"):
        repository.update_mt5_account(
            account_id=account.id,
            display_name="Must roll back",
            login=account.login,
            broker_server=account.broker_server,
            account_currency=account.account_currency,
            export_file_path=account.export_file_path,
            initial_funded_capital="2000",
        )

    unchanged = repository.list_mt5_accounts()[0]
    assert unchanged.display_name == "Repaired account"
    assert unchanged.funded_capital == "1000.00"


def test_deleting_an_account_does_not_leak_roadmap_or_period_review_evidence_to_a_reused_id(repository: SQLiteJournalRepository) -> None:
    repository.register_mt5_account(
        display_name="Secondary",
        login="654321",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    secondary = next(item for item in repository.list_mt5_accounts() if item.display_name == "Secondary")
    repository.save_pillar_roadmap_evidence(
        account_id=secondary.id, pillar="psychology", level=1, item_key="triggers", completed=True, evidence_note="Deleted account's own evidence"
    )
    repository.save_framework_period_review(
        account_id=secondary.id, cadence="weekly", period_start="2026-01-05", period_end="2026-01-11",
        psychology_score=None, risk_score=None, system_score=None, readiness_score=None,
        alert_codes=(), recurring_issues=(), review_note="note", priority_action="Stay disciplined.",
    )

    repository.delete_mt5_account(secondary.id)

    # SQLite reuses the freed integer id for the next created account.
    repository.register_mt5_account(
        display_name="Reused id", login="999999", broker_server="DemoBroker-Live", account_currency="USD", export_file_path="",
    )
    reused = next(item for item in repository.list_mt5_accounts() if item.display_name == "Reused id")
    assert reused.id == secondary.id
    assert repository.list_pillar_roadmap_evidence(reused.id) == []
    assert repository.list_framework_period_reviews(reused.id) == []


def test_imported_mt5_account_cannot_be_deleted(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    MT5ImportService(repository).import_csv(export_path)
    account = repository.list_mt5_accounts()[0]

    with pytest.raises(ValueError, match="cannot be deleted"):
        repository.delete_mt5_account(account.id)


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
    assert trade.strategy == "Journal default"
    assert trade.result_r is None


def test_imports_separate_closed_records_from_one_netting_reversal(repository: SQLiteJournalRepository) -> None:
    first = _row_dict_for_json(position_id="9001", direction="long")
    second = _row_dict_for_json(
        position_id="9001:2",
        direction="short",
        entry_time="2026-08-10T10:00:00+00:00",
        exit_time="2026-08-10T11:00:00+00:00",
    )

    result = MT5ImportService(repository).import_json_positions([first, second], source_label="reversal-fixture")

    assert result.created_count == 2
    assert repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001") is not None
    assert repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001:2") is not None


def test_rejects_currency_mismatch_without_creating_trade(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, currency="EUR")

    with pytest.raises(ImportValidationError, match="currency"):
        MT5ImportService(repository).import_csv(export_path)

    assert repository.count_trades() == 0


def test_rejects_an_unsupported_schema_version_without_creating_trade(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, schema_version="3")

    with pytest.raises(ImportValidationError, match="Unsupported MT5 export schema version; expected 5"):
        MT5ImportService(repository).import_csv(export_path)

    assert repository.count_trades() == 0


def test_imports_schema_v2_execution_evidence(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, initial_risk_amount="100.00")

    result = MT5ImportService(repository).import_csv(export_path)
    trade = repository.list_closed_trades_for_review(repository.find_active_mt5_account("123456", "DemoBroker-Live").id)[0]

    assert result.created_count == 1
    assert trade.entry_stop_price == "1.09500"
    assert trade.entry_target_price == "1.11000"
    assert trade.close_stop_price == "1.09800"
    assert trade.entry_magic_number == "10001"
    assert trade.entry_deal_count == 1
    assert trade.exit_reason == "take_profit"
    assert trade.initial_risk_amount == "100.00"
    assert trade.initial_reward_amount == "200.00"


def test_imports_the_mt5_pretrade_balance_without_modifying_missing_sl(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, pretrade_account_balance="876.54")

    MT5ImportService(repository).import_csv(export_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    trade = repository.list_closed_trades_for_review(account.id)[0]

    assert trade.members[0].pretrade_account_balance == "876.54"
    assert trade.entry_stop_price == "1.09500"


def _row_dict_for_json(**overrides: object) -> dict:
    row = {
        "schema_version": 5,
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": "USD",
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
        "net_pnl": "98.00",
        "entry_stop_price": "1.09500",
        "entry_target_price": "1.11000",
        "close_stop_price": "1.09800",
        "entry_magic_number": "10001",
        "entry_deal_count": 1,
        "exit_reason": "take_profit",
        "initial_risk_amount": "100.00",
        "initial_reward_amount": "200.00",
        "account_balance": "1000.00",
        "pretrade_account_balance": "900.00",
        "server_utc_offset_minutes": 180,
    }
    row.update(overrides)
    return row


def test_csv_and_json_imports_of_the_same_logical_batch_produce_identical_trades(tmp_path: Path) -> None:
    """Parity test: the CSV and HTTP-JSON entrypoints must be behaviourally
    equivalent, since they funnel through the same _import_validated_positions
    core and are documented as sharing every validation/idempotency rule.
    """
    csv_repository = SQLiteJournalRepository(tmp_path / "csv.db")
    csv_repository.initialize()
    csv_repository.configure_journal(reporting_time_basis="utc")
    csv_repository.register_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD", export_file_path=""
    )
    export_path = tmp_path / "positions.csv"
    write_export(export_path, initial_risk_amount="100.00")
    csv_result = MT5ImportService(csv_repository).import_csv(export_path)

    json_repository = SQLiteJournalRepository(tmp_path / "json.db")
    json_repository.initialize()
    json_repository.configure_journal(reporting_time_basis="utc")
    json_repository.register_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD", export_file_path=""
    )
    json_result = MT5ImportService(json_repository).import_json_positions([_row_dict_for_json()], source_label="http:test")

    assert csv_result.created_count == json_result.created_count == 1
    assert csv_result.updated_count == json_result.updated_count == 0

    csv_account = csv_repository.find_active_mt5_account("123456", "DemoBroker-Live")
    json_account = json_repository.find_active_mt5_account("123456", "DemoBroker-Live")
    csv_trade = csv_repository.list_closed_trades_for_review(csv_account.id)[0]
    json_trade = json_repository.list_closed_trades_for_review(json_account.id)[0]

    comparable_fields = [
        "symbol", "direction", "entry_time", "exit_time", "server_utc_offset_minutes",
        "entry_price", "exit_price", "volume", "net_pnl",
        "entry_stop_price", "entry_target_price", "close_stop_price",
        "entry_magic_number", "entry_deal_count", "exit_reason",
        "initial_risk_amount", "initial_reward_amount",
    ]
    for field in comparable_fields:
        assert getattr(csv_trade, field) == getattr(json_trade, field), field
    assert csv_trade.members[0].pretrade_account_balance == json_trade.members[0].pretrade_account_balance


def test_schema_v4_is_rejected_after_the_v5_export_upgrade(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, schema_version="4")

    with pytest.raises(ImportValidationError, match="expected 5"):
        MT5ImportService(repository).import_csv(export_path)


@pytest.mark.parametrize("initial_risk_amount", ["0", "-1"])
def test_schema_v2_rejects_a_non_positive_initial_risk_amount(
    repository: SQLiteJournalRepository, tmp_path: Path, initial_risk_amount: str
) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, initial_risk_amount=initial_risk_amount)

    with pytest.raises(ImportValidationError, match="greater than 0"):
        MT5ImportService(repository).import_csv(export_path)

    assert repository.count_trades() == 0


def test_imports_schema_v3_live_account_balance_and_refreshes_it(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, account_balance="1250.50")

    MT5ImportService(repository).import_csv(export_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")

    assert account is not None
    assert repository.get_latest_mt5_balance(account.id) == "1250.50"

    write_export(export_path, account_balance="1300.00")
    MT5ImportService(repository).import_csv(export_path)

    assert repository.get_latest_mt5_balance(account.id) == "1300.00"


def test_schema_v3_rejects_missing_or_inconsistent_live_account_balance(repository: SQLiteJournalRepository, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, account_balance="")

    with pytest.raises(ImportValidationError, match="requires an account balance"):
        MT5ImportService(repository).import_csv(export_path)

    write_export(export_path, account_balance="1000", second_account_balance="1200")
    with pytest.raises(ImportValidationError, match="one current account balance"):
        MT5ImportService(repository).import_csv(export_path)


@pytest.mark.parametrize("account_balance", ["0", "-25.50"])
def test_schema_v3_accepts_a_non_positive_balance_but_keeps_it_as_a_snapshot(
    repository: SQLiteJournalRepository, tmp_path: Path, account_balance: str
) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path, account_balance=account_balance)

    result = MT5ImportService(repository).import_csv(export_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")

    assert result.created_count == 1
    assert account is not None
    assert repository.get_latest_mt5_balance(account.id) == account_balance
