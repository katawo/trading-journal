from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

from trading_journal.application.live_positions import LivePositionImportService, LivePositionService
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository
from trading_journal.presentation.ongoing import ONGOING_REFRESH_INTERVAL_SECONDS, _position_priority, _risk_label


def _repository(tmp_path) -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.register_mt5_account(
        display_name="Primary", login="123456", broker_server="DemoBroker-Live", account_currency="USD",
        export_file_path="", opening_balance="1000",
    )
    account = repository.get_active_mt5_account()
    assert account is not None
    repository.save_account_risk_policy(
        account_id=account.id, standard_risk_per_trade_percent="1", maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2", weekly_loss_limit_r="4", max_drawdown_percent="10", max_open_risk_r="2",
        max_consecutive_losses=3, minimum_rr="1.5", correlation_policy=None,
    )
    return repository


def _snapshot(
    *, risk: str | None = "10", position_id: str = "9001", positions: bool = True,
    moment: str = "2026-08-18T08:00:00+00:00", entry_price: str = "1.1000",
    current_price: str = "1.1010", stop_price: str = "1.0950", export_interval_seconds: int = 60,
) -> dict:
    row = {
        "schema_version": 1, "account_login": "123456", "broker_server": "DemoBroker-Live", "account_currency": "USD",
        "snapshot_time": moment, "position_id": position_id, "symbol": "EURUSD", "direction": "long",
        "entry_time": "2026-08-18T07:00:00+00:00", "entry_price": entry_price, "current_price": current_price,
        "volume": "1", "stop_price": stop_price, "target_price": "1.1100", "net_unrealized_pnl": "25", "risk_to_stop_amount": risk,
        "magic_number": "10001",
    }
    return {"schema_version": 1, "account_login": "123456", "broker_server": "DemoBroker-Live", "account_currency": "USD", "snapshot_time": moment, "export_interval_seconds": export_interval_seconds, "positions": [row] if positions else []}


def test_live_snapshot_replaces_current_positions_and_does_not_create_trades(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)

    importer.import_snapshot(_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    assert [item.position_id for item in repository.list_live_positions(account.id)] == ["9001"]
    assert repository.list_closed_trades_for_review(account.id) == []

    importer.import_snapshot(_snapshot(positions=False))
    assert repository.list_live_positions(account.id) == []
    assert repository.get_live_snapshot(account.id) is not None


def test_live_snapshot_accepts_a_small_positive_protective_risk(tmp_path) -> None:
    repository = _repository(tmp_path)

    LivePositionImportService(repository).import_snapshot(_snapshot(risk="0.00000001"))

    account = repository.get_active_mt5_account()
    assert account is not None
    assert repository.list_live_positions(account.id)[0].risk_to_stop_amount == "1E-8"


def test_protected_position_risk_is_unknown_without_a_risk_baseline() -> None:
    assert _risk_label(True, None) == "—"
    assert _risk_label(False, None) == "Unprotected"


def test_protected_position_risk_uses_an_unsigned_exposure_label() -> None:
    assert _risk_label(True, Decimal("1.25")) == "1.25R"


def test_ongoing_refresh_and_position_priority_are_action_first() -> None:
    assert ONGOING_REFRESH_INTERVAL_SECONDS == 5
    items = [
        SimpleNamespace(protected=True, risk_r=Decimal("1"), position=SimpleNamespace(symbol="EURUSD", position_id="low")),
        SimpleNamespace(protected=True, risk_r=Decimal("2"), position=SimpleNamespace(symbol="GBPUSD", position_id="high")),
        SimpleNamespace(protected=False, risk_r=None, position=SimpleNamespace(symbol="USDJPY", position_id="unprotected")),
    ]

    assert [item.position.position_id for item in sorted(items, key=_position_priority)] == ["unprotected", "high", "low"]


def test_live_risk_status_and_incidents_only_transition(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)
    account = repository.get_active_mt5_account()
    assert account is not None

    # 20 USD against a 10 USD standard R reaches the 2R limit.
    importer.import_snapshot(_snapshot(risk="20"))
    service = LivePositionService(repository)
    report = service.build_report(account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))
    assert report.status == "stop"
    assert str(report.total_risk_r) == "2"
    assert [(item.category, item.state) for item in repository.list_live_position_incidents(account.id)] == [("stop", "opened")]

    service.build_report(account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))
    assert len(repository.list_live_position_incidents(account.id)) == 1

    importer.import_snapshot(_snapshot(risk=None))
    report = service.build_report(account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))
    assert report.status == "unprotected"
    assert [(item.category, item.state) for item in repository.list_live_position_incidents(account.id)] == [
        ("stop", "resolved"), ("unprotected", "opened"), ("stop", "opened"),
    ]


def test_live_csv_envelope_accepts_an_empty_flat_snapshot(tmp_path) -> None:
    repository = _repository(tmp_path)
    header = [
        "record_type", "schema_version", "account_login", "broker_server", "account_currency", "snapshot_time",
        "position_id", "symbol", "direction", "entry_time", "entry_price", "current_price", "volume",
        "stop_price", "target_price", "net_unrealized_pnl", "risk_to_stop_amount", "magic_number",
    ]
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    writer.writerow({
        "record_type": "snapshot", "schema_version": "1", "account_login": "123456", "broker_server": "DemoBroker-Live",
        "account_currency": "USD", "snapshot_time": "2026-08-18T08:00:00+00:00",
    })

    LivePositionImportService(repository).import_csv_bytes("ignored.csv", buffer.getvalue().encode())
    account = repository.get_active_mt5_account()
    assert account is not None
    assert repository.list_live_positions(account.id) == []
    assert repository.get_live_snapshot(account.id) is not None


def test_live_trailing_stop_above_entry_is_protected_and_older_snapshot_cannot_replace_it(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)
    account = repository.get_active_mt5_account()
    assert account is not None
    newer = _snapshot(risk="15", entry_price="1.1000", current_price="1.1050", stop_price="1.1020")

    importer.import_snapshot(newer)
    report = LivePositionService(repository).build_report(account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))
    assert report.unprotected_count == 0

    older = _snapshot(position_id="older", moment="2026-08-18T07:59:59+00:00")
    importer.import_snapshot(older)
    assert [row.position_id for row in repository.list_live_positions(account.id)] == ["9001"]


def test_live_snapshot_uses_its_export_interval_for_freshness(tmp_path) -> None:
    repository = _repository(tmp_path)
    account = repository.get_active_mt5_account()
    assert account is not None
    LivePositionImportService(repository).import_snapshot(_snapshot(export_interval_seconds=10))

    service = LivePositionService(repository)
    assert service.build_report(account.id, now=datetime(2026, 8, 18, 8, 0, 20, tzinfo=timezone.utc)).status == "within"
    assert service.build_report(account.id, now=datetime(2026, 8, 18, 8, 0, 21, tzinfo=timezone.utc)).status == "stale"


def test_deleting_an_unimported_account_removes_disposable_live_state(tmp_path) -> None:
    repository = _repository(tmp_path)
    account = repository.get_active_mt5_account()
    assert account is not None
    importer = LivePositionImportService(repository)
    importer.import_snapshot(_snapshot(risk=None))
    LivePositionService(repository).build_report(account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))

    repository.delete_mt5_account(account.id)

    assert repository.list_mt5_accounts() == []
