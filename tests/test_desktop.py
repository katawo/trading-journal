from __future__ import annotations

import csv
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from trading_journal.application.auto_sync import MT5AutoSyncResult
from trading_journal.desktop import (
    DesktopInstanceLock,
    DesktopSyncControl,
    DesktopSyncStatusStore,
    DesktopSyncWorker,
    desktop_data_directory,
    desktop_headless,
    desktop_server_port,
    desktop_window_enabled,
    desktop_runtime_paths,
    reset_desktop_database,
    run_desktop_window,
    self_check,
    _database_is_ready,
    _terminate,
)
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
    "server_utc_offset_minutes",
    "entry_price",
    "exit_price",
    "volume",
    "gross_pnl",
    "commission",
    "swap",
    "fees",
    "net_pnl",
    "entry_stop_price",
    "entry_target_price",
    "close_stop_price",
    "entry_magic_number",
    "entry_deal_count",
    "exit_reason",
    "initial_risk_amount",
    "initial_reward_amount",
    "account_balance",
    "pretrade_account_balance",
]


def write_export(path: Path) -> None:
    row = {
        "schema_version": "5",
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": "USD",
        "position_id": "9001",
        "symbol": "EURUSD",
        "direction": "long",
        "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T10:00:00+00:00",
        "server_utc_offset_minutes": "0",
        "entry_price": "1.10000",
        "exit_price": "1.10100",
        "volume": "1.00",
        "gross_pnl": "100.00",
        "commission": "-1.50",
        "swap": "-0.25",
        "fees": "-0.25",
        "net_pnl": "98.00",
        "entry_stop_price": "",
        "entry_target_price": "",
        "close_stop_price": "",
        "entry_magic_number": "",
        "entry_deal_count": "",
        "exit_reason": "client",
        "initial_risk_amount": "",
        "initial_reward_amount": "",
        "account_balance": "1000.00",
        "pretrade_account_balance": "",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerow(row)


def configured_repository(tmp_path: Path, export_path: Path) -> SQLiteJournalRepository:
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Primary",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path=str(export_path),
    )
    return repository


def test_desktop_data_directory_uses_platform_conventions_and_override(tmp_path: Path) -> None:
    assert desktop_data_directory(environment={"LOCALAPPDATA": "C:/Users/trader/AppData/Local"}, home=tmp_path, platform="win32") == Path("C:/Users/trader/AppData/Local") / "TradingJournal"
    assert desktop_data_directory(environment={"XDG_DATA_HOME": "/var/local-data"}, home=tmp_path, platform="linux") == Path("/var/local-data/trading-journal")
    assert desktop_data_directory(environment={"TRADING_JOURNAL_DESKTOP_DATA_DIR": str(tmp_path / "journal")}, home=tmp_path, platform="linux") == tmp_path / "journal"


def test_desktop_server_port_uses_an_optional_explicit_port() -> None:
    assert desktop_server_port({"TRADING_JOURNAL_DESKTOP_PORT": "18501"}) == 18501
    with pytest.raises(RuntimeError, match="valid TCP port"):
        desktop_server_port({"TRADING_JOURNAL_DESKTOP_PORT": "not-a-port"})
    with pytest.raises(RuntimeError, match="between 1 and 65535"):
        desktop_server_port({"TRADING_JOURNAL_DESKTOP_PORT": "70000"})


def test_desktop_lock_discards_a_legacy_lock_when_its_pid_was_reused(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    lock_path = tmp_path / "desktop.lock"
    lock_path.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(desktop, "_process_is_running", lambda process_id: process_id == 12345)
    monkeypatch.setattr(desktop, "_process_looks_like_desktop", lambda _process_id: False)

    lock = DesktopInstanceLock(lock_path)
    lock.acquire()

    assert ":" in lock_path.read_text(encoding="utf-8")
    lock.release()
    assert not lock_path.exists()


def test_desktop_lock_keeps_a_current_process_identity(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    lock_path = tmp_path / "desktop.lock"
    lock_path.write_text("12345:linux:stable-start-time", encoding="utf-8")
    monkeypatch.setattr(desktop, "_process_is_running", lambda process_id: process_id == 12345)
    monkeypatch.setattr(desktop, "_process_start_identity", lambda process_id: "linux:stable-start-time" if process_id == 12345 else None)

    with pytest.raises(RuntimeError, match="already running"):
        DesktopInstanceLock(lock_path).acquire()


def test_process_probe_treats_an_invalid_windows_handle_as_not_running(monkeypatch) -> None:
    from trading_journal import desktop

    def invalid_handle(_process_id: int, _signal: int) -> None:
        raise OSError(6, "The handle is invalid")

    monkeypatch.setattr(desktop.os, "kill", invalid_handle)

    assert desktop._process_is_running(12345) is False


def test_desktop_server_disables_the_source_file_watcher(monkeypatch) -> None:
    from streamlit.web import bootstrap
    from trading_journal import desktop

    captured: dict[str, object] = {}
    monkeypatch.setattr(desktop, "application_entrypoint", lambda: Path(__file__))
    monkeypatch.setattr(bootstrap, "load_config_options", lambda *, flag_options: captured.update(flag_options))
    monkeypatch.setattr(bootstrap, "run", lambda *_args, **_kwargs: None)

    desktop.run_streamlit_server(18501)

    assert captured["server_fileWatcherType"] == "none"


def test_desktop_window_is_enabled_for_windows_unless_browser_fallback_is_requested() -> None:
    assert desktop_window_enabled(environment={}, platform="win32") is True
    assert desktop_window_enabled(environment={}, platform="linux") is False
    assert desktop_window_enabled(environment={"TRADING_JOURNAL_DESKTOP_BROWSER": "1"}, platform="win32") is False
    assert desktop_window_enabled(environment={"TRADING_JOURNAL_DESKTOP_HEADLESS": "1"}, platform="win32") is False
    assert desktop_window_enabled(environment={}, platform="darwin") is False
    assert desktop_headless({"TRADING_JOURNAL_DESKTOP_HEADLESS": "1"}) is True
    assert desktop_headless({}) is False


def test_desktop_database_probe_releases_its_engine(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    calls: list[str] = []

    class Repository:
        def __init__(self, _path: Path) -> None:
            pass

        def initialize(self) -> None:
            calls.append("initialize")

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(desktop, "SQLiteJournalRepository", Repository)

    assert _database_is_ready(tmp_path / "journal.db") == (True, None)
    assert calls == ["initialize", "close"]


def test_desktop_window_opens_the_local_server_in_a_native_webview(monkeypatch) -> None:
    calls: dict[str, object] = {}
    webview = SimpleNamespace(
        create_window=lambda *args, **kwargs: calls.update(args=args, kwargs=kwargs),
        start=lambda: calls.update(started=True),
    )
    monkeypatch.setitem(sys.modules, "webview", webview)

    run_desktop_window("http://127.0.0.1:18501")

    assert calls["args"] == ("Trade Compass", "http://127.0.0.1:18501")
    assert calls["kwargs"] == {"width": 1440, "height": 920, "min_size": (1024, 700)}
    assert calls["started"] is True


def test_parent_watchdog_exits_once_the_launching_supervisor_is_gone(monkeypatch) -> None:
    from trading_journal import desktop

    monkeypatch.setattr(desktop, "_process_is_running", lambda process_id: False)
    exit_calls: list[int] = []
    monkeypatch.setattr(desktop.os, "_exit", exit_calls.append)

    desktop._watch_parent_process(12345, poll_seconds=0)

    assert exit_calls == [1]


def test_parent_watchdog_is_not_started_without_a_parent_pid(monkeypatch) -> None:
    from trading_journal import desktop

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("no watchdog thread should be started without a parent pid")

    monkeypatch.setattr(desktop.threading, "Thread", fail_if_constructed)

    desktop._start_parent_watchdog(None)


def test_run_streamlit_server_starts_a_parent_watchdog(monkeypatch) -> None:
    from streamlit.web import bootstrap
    from trading_journal import desktop

    watchdog_calls: list[int | None] = []
    monkeypatch.setattr(desktop, "application_entrypoint", lambda: Path(__file__))
    monkeypatch.setattr(desktop, "_start_parent_watchdog", watchdog_calls.append)
    monkeypatch.setattr(bootstrap, "load_config_options", lambda *, flag_options: None)
    monkeypatch.setattr(bootstrap, "run", lambda *_args, **_kwargs: None)

    desktop.run_streamlit_server(18501, 4242)

    assert watchdog_calls == [4242]


def test_run_desktop_window_starts_a_parent_watchdog(monkeypatch) -> None:
    from trading_journal import desktop

    watchdog_calls: list[int | None] = []
    monkeypatch.setattr(desktop, "_start_parent_watchdog", watchdog_calls.append)
    webview = SimpleNamespace(create_window=lambda *args, **kwargs: None, start=lambda: None)
    monkeypatch.setitem(sys.modules, "webview", webview)

    desktop.run_desktop_window("http://127.0.0.1:18501", 4242)

    assert watchdog_calls == [4242]


def test_desktop_termination_reaps_a_forcibly_killed_child() -> None:
    class UnresponsiveProcess:
        def __init__(self) -> None:
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, *, timeout: float) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("desktop", timeout)
            return 0

        def kill(self) -> None:
            self.killed = True

    process = UnresponsiveProcess()

    _terminate(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2


def test_desktop_status_preserves_last_import_and_rebuilds_results(tmp_path: Path) -> None:
    store = DesktopSyncStatusStore(tmp_path / "status.json")
    imported = MT5AutoSyncResult("Primary", "123456", "DemoBroker-Live", "positions.csv", "imported", created_count=2, updated_count=1)
    store.write([imported])
    first_import_at = store.last_import_at()

    store.write([MT5AutoSyncResult("Primary", "123456", "DemoBroker-Live", "positions.csv", "up_to_date")])

    assert first_import_at is not None
    assert store.last_import_at() == first_import_at
    assert [(item.account_name, item.status) for item in store.results()] == [("Primary", "up_to_date")]


def test_desktop_sync_control_consumes_a_manual_request_and_keeps_shutdown_separate(tmp_path: Path) -> None:
    control = DesktopSyncControl(tmp_path / "sync.request", tmp_path / "shutdown.request", tmp_path / "reset.request")

    control.request_sync()
    assert control.consume_sync_request() is True
    assert control.consume_sync_request() is False
    assert control.shutdown_requested() is False

    control.request_shutdown()
    assert control.shutdown_requested() is True
    control.clear_shutdown_request()
    assert control.shutdown_requested() is False

    control.request_reset()
    assert control.consume_reset_request() is True
    assert control.consume_reset_request() is False


def test_reset_desktop_database_removes_only_journal_database_state(tmp_path: Path) -> None:
    paths = desktop_runtime_paths(environment={"TRADING_JOURNAL_DESKTOP_DATA_DIR": str(tmp_path / "desktop-data")}, home=tmp_path, platform="linux")
    paths.data_directory.mkdir(parents=True)
    for path in (
        paths.database_path,
        Path(f"{paths.database_path}-wal"),
        Path(f"{paths.database_path}-shm"),
        paths.sync_status_path,
        paths.sync_request_path,
    ):
        path.write_text("replaceable", encoding="utf-8")
    paths.log_path.write_text("keep", encoding="utf-8")
    preserved = paths.data_directory / "positions.csv"
    preserved.write_text("keep", encoding="utf-8")

    reset_desktop_database(paths)

    assert not paths.database_path.exists()
    assert not Path(f"{paths.database_path}-wal").exists()
    assert not Path(f"{paths.database_path}-shm").exists()
    assert not paths.sync_status_path.exists()
    assert not paths.sync_request_path.exists()
    assert paths.log_path.read_text(encoding="utf-8") == "keep"
    assert preserved.read_text(encoding="utf-8") == "keep"


def test_desktop_worker_uses_the_existing_hash_based_mt5_importer(tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    repository = configured_repository(tmp_path, export_path)
    status = DesktopSyncStatusStore(tmp_path / "status.json")
    worker = DesktopSyncWorker(tmp_path / "journal.db", status, DesktopSyncControl(tmp_path / "sync.request", tmp_path / "shutdown.request"))

    first = worker.sync_once()
    second = worker.sync_once()

    assert [(item.status, item.created_count) for item in first] == [("imported", 1)]
    assert [(item.status, item.created_count) for item in second] == [("up_to_date", 0)]
    assert repository.count_trades() == 1
    assert status.last_import_at() is not None


def test_desktop_self_check_uses_configured_writable_data_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(tmp_path / "desktop-data"))

    assert self_check() == 0
    assert desktop_runtime_paths().data_directory.is_dir()


def test_desktop_dashboard_delegates_manual_sync_to_the_background_worker(monkeypatch, tmp_path: Path) -> None:
    export_path = tmp_path / "positions.csv"
    write_export(export_path)
    repository = configured_repository(tmp_path, export_path)
    data_directory = tmp_path / "desktop-data"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(data_directory))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    next(item for item in app.button if item.label == "Sync MT5 now").click().run()

    assert not app.exception
    assert (data_directory / "mt5-sync.request").is_file()
    assert repository.count_trades() == 0
    assert any("Desktop sync requested" in item.value for item in app.info)
