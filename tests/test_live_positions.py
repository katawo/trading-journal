from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
import sqlite3
from types import SimpleNamespace

import pytest

from trading_journal.application.live_positions import LivePositionImportService, LivePositionService
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import CURRENT_SCHEMA_VERSION, SQLiteJournalRepository
from trading_journal.presentation.ongoing import (
    ONGOING_REFRESH_INTERVAL_SECONDS,
    _compact_stat_html,
    _live_activity_indicator,
    _pnl_metric,
    _position_priority,
    _render_inline_position_state,
    _risk_buffer_metric,
    _risk_label,
    _risk_metric,
    _unprotected_metric,
)


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


def _closed(
    position_id: str,
    *,
    net_pnl: str = "10",
    direction: str = "long",
    entry_time: str = "2026-08-18T07:00:00+00:00",
) -> MT5PositionExport:
    return MT5PositionExport(
        schema_version=5,
        account_login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        position_id=position_id,
        symbol="EURUSD",
        direction=direction,
        entry_time=entry_time,
        exit_time="2026-08-18T09:00:00+00:00",
        entry_price="1.1000",
        exit_price="1.1010",
        volume="1",
        gross_pnl=net_pnl,
        commission="0",
        swap="0",
        fees="0",
        net_pnl=net_pnl,
        initial_risk_amount="10",
        server_utc_offset_minutes=0,
    )


def _two_position_snapshot() -> dict:
    payload = _snapshot(position_id="9001")
    second = dict(payload["positions"][0])
    second.update({"position_id": "9002", "entry_time": "2026-08-18T07:05:00+00:00", "net_unrealized_pnl": "15"})
    payload["positions"].append(second)
    return payload


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


def test_live_group_survives_snapshots_and_becomes_one_trade_only_after_every_member_closes(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)
    importer.import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    service = LivePositionService(repository)

    logical_trade_id = service.create_logical_trade(
        account.id,
        ("9001", "9002"),
        "London scale-in",
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )
    report = service.build_report(account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))
    group = next(item for item in report.logical_trades if item.logical_trade_id == logical_trade_id)
    assert group.display_label == "London scale-in"
    assert group.open_count == 2
    assert group.net_unrealized_pnl == Decimal("40")
    assert group.unrealized_pnl_r == Decimal("4")
    assert group.open_risk_r == Decimal("2")

    importer.import_snapshot(_snapshot(position_id="9002", moment="2026-08-18T08:01:00+00:00"))
    repository.upsert_mt5_positions(account.id, [_closed("9001")], "positions.csv", "one")
    assert repository.list_closed_trades_for_review(account.id) == []
    assert repository.list_imported_positions_for_grouping(account.id) == []
    assert repository.realized_pnl_on(account.id, date(2026, 8, 18), "utc") == "0"
    imported_member = repository.list_imported_positions_for_risk(account.id)[0]
    with pytest.raises(ValueError, match="must finish importing"):
        repository.update_logical_trade_group(
            account_id=account.id,
            logical_trade_id=logical_trade_id,
            position_trade_ids=(imported_member.id,),
            display_label=None,
        )
    pending = repository.list_pending_logical_trades(account.id)[0]
    assert pending.imported_position_ids == ("9001",)

    repository.upsert_mt5_positions(
        account.id,
        [_closed("9001"), _closed("9002", entry_time="2026-08-18T07:05:00+00:00")],
        "positions.csv",
        "two",
    )
    completed = repository.list_closed_trades_for_review(account.id)
    assert repository.list_pending_logical_trades(account.id) == []
    assert len(completed) == 1
    assert completed[0].id == logical_trade_id
    assert completed[0].position_ids == ("9001", "9002")
    assert completed[0].display_label == "London scale-in"


def test_legacy_live_group_attaches_the_current_netting_lifecycle_after_reversals(tmp_path) -> None:
    repository = _repository(tmp_path)
    payload = _two_position_snapshot()
    payload["positions"][0]["entry_time"] = "2026-08-18T07:00:00+00:00"
    LivePositionImportService(repository).import_snapshot(payload)
    account = repository.get_active_mt5_account()
    assert account is not None
    logical_trade_id = repository.create_pending_logical_trade(
        account_id=account.id,
        position_ids=("9001", "9002"),
        display_label="Current long idea",
    )

    repository.upsert_mt5_positions(
        account.id,
        [
            _closed("9001", direction="short", entry_time="2026-08-18T06:00:00+00:00"),
            _closed("9001:2", entry_time="2026-08-18T07:00:00+00:00"),
            _closed("9002", entry_time="2026-08-18T07:05:00+00:00"),
        ],
        "positions.csv",
        "reversal",
    )

    completed = repository.list_closed_trades_for_review(account.id)
    grouped = next(item for item in completed if item.id == logical_trade_id)
    assert grouped.position_ids == ("9001:2", "9002")
    assert grouped.direction == "long"
    assert any(item.position_ids == ("9001",) and item.direction == "short" for item in completed)
    assert repository.list_pending_logical_trades(account.id) == []


def test_live_group_keeps_one_risk_policy_version_when_policy_changes_before_close(tmp_path) -> None:
    repository = _repository(tmp_path)
    LivePositionImportService(repository).import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    original_policy = repository.get_active_risk_policy(account.id)
    assert original_policy is not None
    logical_trade_id = repository.create_pending_logical_trade(
        account_id=account.id,
        position_ids=("9001", "9002"),
        display_label=None,
    )
    repository.save_account_risk_policy(
        account_id=account.id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="3",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
        expected_active_policy_id=original_policy.id,
        confirm_recalculation=True,
    )

    repository.upsert_mt5_positions(
        account.id,
        [_closed("9001"), _closed("9002", entry_time="2026-08-18T07:05:00+00:00")],
        "positions.csv",
        "all",
    )

    completed = next(item for item in repository.list_closed_trades_for_review(account.id) if item.id == logical_trade_id)
    assert {member.auto_risk_policy_id for member in completed.members} == {original_policy.id}


def test_pending_group_can_change_open_members_but_keeps_a_closed_member_fixed(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)
    importer.import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    service = LivePositionService(repository)
    logical_trade_id = service.create_logical_trade(
        account.id,
        ("9001", "9002"),
        None,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    third = _snapshot(position_id="9003", moment="2026-08-18T08:01:00+00:00")["positions"][0]
    third["entry_time"] = "2026-08-18T06:45:00+00:00"
    remaining = _snapshot(position_id="9002", moment="2026-08-18T08:01:00+00:00")["positions"][0]
    importer.import_snapshot({
        "schema_version": 1,
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": "USD",
        "snapshot_time": "2026-08-18T08:01:00+00:00",
        "export_interval_seconds": 60,
        "positions": [remaining, third],
    })
    repository.upsert_mt5_positions(account.id, [_closed("9001")], "positions.csv", "one")

    service.update_logical_trade(
        account.id,
        logical_trade_id,
        ("9003",),
        "Extended scale-in",
        now=datetime(2026, 8, 18, 8, 1, tzinfo=timezone.utc),
    )

    pending = repository.list_pending_logical_trades(account.id)[0]
    assert set(pending.position_ids) == {"9001", "9003"}
    assert pending.display_label == "Extended scale-in"
    assert pending.first_entry_time == "2026-08-18T06:45:00+00:00"
    report = service.build_report(account.id, now=datetime(2026, 8, 18, 8, 1, tzinfo=timezone.utc))
    assert any(item.display_label == "#9002" for item in report.logical_trades)


def test_new_group_member_must_overlap_a_still_open_member(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)
    importer.import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    service = LivePositionService(repository)
    logical_trade_id = service.create_logical_trade(
        account.id,
        ("9001", "9002"),
        None,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )
    importer.import_snapshot(_snapshot(position_id="9003", moment="2026-08-18T08:01:00+00:00"))

    with pytest.raises(ValueError, match="must overlap"):
        service.update_logical_trade(
            account.id,
            logical_trade_id,
            ("9003",),
            None,
            now=datetime(2026, 8, 18, 8, 1, tzinfo=timezone.utc),
        )


def test_live_group_rejects_incompatible_positions_and_stale_grouping(tmp_path) -> None:
    repository = _repository(tmp_path)
    importer = LivePositionImportService(repository)
    payload = _two_position_snapshot()
    payload["positions"][1]["symbol"] = "GBPUSD"
    importer.import_snapshot(payload)
    account = repository.get_active_mt5_account()
    assert account is not None
    service = LivePositionService(repository)

    with pytest.raises(ValueError, match="same symbol and direction"):
        service.create_logical_trade(
            account.id,
            ("9001", "9002"),
            None,
            now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="stale"):
        service.create_logical_trade(
            account.id,
            ("9001", "9002"),
            None,
            now=datetime(2026, 8, 18, 8, 3, tzinfo=timezone.utc),
        )


def test_all_open_live_group_can_be_disbanded(tmp_path) -> None:
    repository = _repository(tmp_path)
    LivePositionImportService(repository).import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    service = LivePositionService(repository)
    logical_trade_id = service.create_logical_trade(
        account.id,
        ("9001", "9002"),
        None,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    service.disband_logical_trade(
        account.id,
        logical_trade_id,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    assert repository.list_pending_logical_trades(account.id) == []
    assert [item.display_label for item in service.build_report(
        account.id, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
    ).logical_trades] == ["#9001", "#9002"]


def test_live_snapshot_accepts_a_small_positive_protective_risk(tmp_path) -> None:
    repository = _repository(tmp_path)

    LivePositionImportService(repository).import_snapshot(_snapshot(risk="0.00000001"))

    account = repository.get_active_mt5_account()
    assert account is not None
    assert repository.list_live_positions(account.id)[0].risk_to_stop_amount == "1E-8"


def test_live_activity_twinkles_only_for_fresh_open_positions() -> None:
    active = SimpleNamespace(positions=(object(),), status="within")
    caution = SimpleNamespace(positions=(object(),), status="caution")
    stale = SimpleNamespace(positions=(object(),), status="stale")
    flat = SimpleNamespace(positions=(), status="within")

    assert 'class="ongoing-live-pulse"' in _live_activity_indicator(active)
    assert 'class="ongoing-live-pulse"' in _live_activity_indicator(caution)
    assert _live_activity_indicator(stale) == ""
    assert _live_activity_indicator(flat) == ""


def test_break_even_or_profitable_stop_reports_zero_open_risk(tmp_path) -> None:
    repository = _repository(tmp_path)
    LivePositionImportService(repository).import_snapshot(
        _snapshot(risk="0", entry_price="1.1000", current_price="1.1050", stop_price="1.1020")
    )
    account = repository.get_active_mt5_account()
    assert account is not None

    report = LivePositionService(repository).build_report(
        account.id,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    assert report.total_risk_r == Decimal("0")
    assert report.positions[0].protected is True
    assert report.positions[0].risk_amount_available is True
    assert report.risk_unavailable_count == 0
    assert _risk_metric(report) == ("0.00R", "Within limit", "green", "2.00R account limit")


def test_open_risk_is_unavailable_when_no_position_risk_can_be_calculated(tmp_path) -> None:
    repository = _repository(tmp_path)
    LivePositionImportService(repository).import_snapshot(_snapshot(risk=None))
    account = repository.get_active_mt5_account()
    assert account is not None

    report = LivePositionService(repository).build_report(
        account.id,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    assert report.total_risk_r is None
    assert report.positions[0].protected is True
    assert report.positions[0].risk_amount_available is False
    assert report.unprotected_count == 0
    assert report.risk_unavailable_count == 1
    assert report.status == "risk_unavailable"
    assert _unprotected_metric(report) == ("0", "All protected", "green")
    assert _risk_metric(report) == (None, "1 position risk unavailable", "orange", "2.00R account limit")
    assert [(item.category, item.state) for item in repository.list_live_position_incidents(account.id)] == [
        ("risk_unavailable", "opened"),
    ]


def test_known_limit_breach_takes_priority_over_additional_unavailable_risk(tmp_path) -> None:
    repository = _repository(tmp_path)
    payload = _snapshot(risk="20")
    unavailable = _snapshot(risk=None, position_id="9002")["positions"][0]
    payload["positions"].append(unavailable)
    LivePositionImportService(repository).import_snapshot(payload)
    account = repository.get_active_mt5_account()
    assert account is not None

    report = LivePositionService(repository).build_report(
        account.id,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    assert report.status == "stop"
    assert report.total_risk_r == Decimal("2")
    assert report.risk_unavailable_count == 1
    assert _risk_metric(report) == ("2.00R", "Limit reached", "red", "2.00R account limit")


def test_empty_snapshot_reports_real_zeroes_but_missing_snapshot_reports_unavailable(tmp_path) -> None:
    repository = _repository(tmp_path)
    account = repository.get_active_mt5_account()
    assert account is not None
    missing = LivePositionService(repository).build_report(account.id)

    assert missing.total_risk_r is None
    assert _unprotected_metric(missing) == (None, "Unavailable", "gray")
    assert _pnl_metric(missing, "USD") == (None, "Unavailable", "gray")

    LivePositionImportService(repository).import_snapshot(_snapshot(positions=False))
    flat = LivePositionService(repository).build_report(
        account.id,
        now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    assert flat.total_risk_r == Decimal("0")
    assert _risk_metric(flat) == ("0.00R", "No open risk", "gray", "2.00R account limit")
    assert _unprotected_metric(flat) == ("0", "No open positions", "gray")
    assert _pnl_metric(flat, "USD") == ("$0.00", "Flat", "gray")


def test_live_metric_colors_follow_risk_and_floating_pnl_state() -> None:
    report = SimpleNamespace(
        snapshot_time=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        positions=(object(), object()),
        unprotected_count=0,
        risk_unavailable_count=0,
        total_risk_r=Decimal("0.85"),
        limit_r=Decimal("1"),
        status="caution",
        net_unrealized_pnl=Decimal("3.12"),
    )

    assert _risk_metric(report) == ("0.85R", "Near limit", "orange", "1.00R account limit")
    assert _unprotected_metric(report) == ("0", "All protected", "green")
    assert _pnl_metric(report, "USD") == ("+$3.12", "Profit", "green")

    report.status = "stop"
    report.total_risk_r = Decimal("1.25")
    report.unprotected_count = 1
    report.risk_unavailable_count = 1
    report.net_unrealized_pnl = Decimal("-3.12")
    assert _risk_metric(report) == ("1.25R", "Over limit", "red", "1.00R account limit")
    assert _pnl_metric(report, "USD") == ("−$3.12", "Loss", "red")


def test_risk_buffer_reports_headroom_and_refuses_false_precision() -> None:
    report = SimpleNamespace(
        snapshot_time=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        status="within",
        total_risk_r=Decimal("0.24"),
        limit_r=Decimal("1"),
        unprotected_count=0,
        risk_unavailable_count=0,
    )

    assert _risk_buffer_metric(report) == ("0.76R", "24% of limit used", "positive")

    report.unprotected_count = 1
    assert _risk_buffer_metric(report) == (None, "Known risk is a lower bound", "negative")


def test_risk_buffer_uses_displayed_utilization_for_threshold_tone() -> None:
    report = SimpleNamespace(
        snapshot_time=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        status="within",
        total_risk_r=Decimal("0.7996"),
        limit_r=Decimal("1"),
        unprotected_count=0,
        risk_unavailable_count=0,
    )

    assert _risk_buffer_metric(report) == ("0.20R", "80% of limit used", "warning")

    report.total_risk_r = Decimal("0.9996")
    assert _risk_buffer_metric(report) == ("0.00R", "100% of limit used", "negative")


def test_risk_buffer_utilization_note_uses_the_metric_tone() -> None:
    markup = _compact_stat_html(
        ("Risk buffer", "0.00R", "negative", "152.8% of limit used"),
        note_tone="negative",
    )

    assert (
        '<div class="dashboard-stat-note dashboard-stat-tone-negative">'
        "152.8% of limit used</div>"
    ) in markup


def test_stale_snapshot_withholds_risk_buffer_and_affirmative_empty_state(monkeypatch) -> None:
    report = SimpleNamespace(
        snapshot_time=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        status="stale",
        total_risk_r=Decimal("0.24"),
        limit_r=Decimal("1"),
        unprotected_count=0,
        risk_unavailable_count=0,
    )
    rendered: list[str] = []
    monkeypatch.setattr("trading_journal.presentation.ongoing.st.markdown", rendered.append)

    assert _risk_buffer_metric(report) == (None, "Snapshot stale", "warning")
    _render_inline_position_state(report)

    assert rendered == [
        ":material/warning: **Snapshot stale.** Latest position state is unavailable until a fresh MT5 snapshot arrives."
    ]


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

    importer.import_snapshot(_snapshot(risk=None, stop_price=""))
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


def test_deleting_an_unimported_account_removes_pending_logical_trade_state(tmp_path) -> None:
    repository = _repository(tmp_path)
    LivePositionImportService(repository).import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    repository.create_pending_logical_trade(
        account_id=account.id,
        position_ids=("9001", "9002"),
        display_label=None,
    )

    repository.delete_mt5_account(account.id)

    assert repository.list_mt5_accounts() == []


def test_schema_v5_upgrade_creates_pending_group_tables_and_backup(tmp_path) -> None:
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE pending_logical_trade_members")
        connection.execute("DROP TABLE pending_logical_trades")
        connection.execute("PRAGMA user_version = 5")

    migrated = SQLiteJournalRepository(database_path)
    migrated.initialize()
    migrated.close()

    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert {"pending_logical_trades", "pending_logical_trade_members"}.issubset(tables)
    assert version == CURRENT_SCHEMA_VERSION
    assert len(list(tmp_path.glob(f"journal.pre-schema-v{CURRENT_SCHEMA_VERSION}-*.db.bak"))) == 1


def test_schema_v6_upgrade_backfills_pending_member_entry_times(tmp_path) -> None:
    database_path = tmp_path / "journal.db"
    repository = _repository(tmp_path)
    LivePositionImportService(repository).import_snapshot(_two_position_snapshot())
    account = repository.get_active_mt5_account()
    assert account is not None
    repository.create_pending_logical_trade(
        account_id=account.id,
        position_ids=("9001", "9002"),
        display_label=None,
    )
    repository.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE pending_logical_trade_members DROP COLUMN entry_time")
        connection.execute("PRAGMA user_version = 6")

    migrated = SQLiteJournalRepository(database_path)
    migrated.initialize()

    pending = migrated.list_pending_logical_trades(account.id)[0]
    with sqlite3.connect(database_path) as connection:
        entry_times = {
            row[0]
            for row in connection.execute(
                "SELECT entry_time FROM pending_logical_trade_members ORDER BY mt5_position_id"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert entry_times == {
        "2026-08-18T07:00:00+00:00",
        "2026-08-18T07:05:00+00:00",
    }
    assert pending.first_entry_time == "2026-08-18T07:00:00+00:00"
    assert version == CURRENT_SCHEMA_VERSION
    assert len(list(tmp_path.glob(f"journal.pre-schema-v{CURRENT_SCHEMA_VERSION}-*.db.bak"))) == 1
