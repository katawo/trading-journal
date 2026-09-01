from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from trading_journal.application.live_positions import LivePositionImportService, LivePositionService
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository
from trading_journal.presentation.ongoing import (
    ONGOING_REFRESH_INTERVAL_SECONDS,
    _compact_stat_html,
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


def test_live_refresh_is_isolated_from_the_editable_today_workspace() -> None:
    source = (Path(__file__).parents[1] / "src/trading_journal/presentation/ongoing.py").read_text(encoding="utf-8")

    assert "@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)\ndef _render_live_positions" in source
    assert "@st.fragment(run_every=ONGOING_REFRESH_INTERVAL_SECONDS)\ndef render_ongoing_positions_page" not in source
    assert source.index("_render_live_positions(repo, account)") < source.index("_render_today_action_center(repo, account)")


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
