from __future__ import annotations

import csv
from pathlib import Path
import subprocess
import sys
import threading
import time
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


def _hold_lock_in_subprocess(lock_path: Path) -> subprocess.Popen[str]:
    """Own the lock from a separate process so the kernel is the arbiter."""

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys;"
            "from trading_journal.desktop import DesktopInstanceLock;"
            "lock = DesktopInstanceLock(__import__('pathlib').Path(sys.argv[1]));"
            "lock.acquire();"
            "print('held', flush=True);"
            "__import__('time').sleep(120)",
            str(lock_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "held", process.communicate()[1]
    return process


def test_desktop_lock_blocks_a_second_process_while_the_owner_lives(tmp_path: Path) -> None:
    from trading_journal import desktop

    lock_path = tmp_path / "desktop.lock"
    owner = _hold_lock_in_subprocess(lock_path)
    try:
        with pytest.raises(desktop.DesktopAlreadyRunningError, match="already running"):
            DesktopInstanceLock(lock_path).acquire()
    finally:
        owner.kill()
        owner.wait(timeout=10)


def test_desktop_lock_is_released_by_the_kernel_when_the_owner_is_force_killed(tmp_path: Path) -> None:
    """A SIGKILLed owner must not leave a lock that blocks the next launch."""

    lock_path = tmp_path / "desktop.lock"
    owner = _hold_lock_in_subprocess(lock_path)
    owner.kill()
    owner.wait(timeout=10)

    lock = DesktopInstanceLock(lock_path)
    lock.acquire()
    lock.release()


def test_desktop_lock_can_be_reacquired_after_a_clean_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "desktop.lock"
    first = DesktopInstanceLock(lock_path)
    first.acquire()
    first.release()

    second = DesktopInstanceLock(lock_path)
    second.acquire()
    second.release()


def _force_pid_file_lock(monkeypatch) -> None:
    """Exercise the fallback used only where neither fcntl nor msvcrt exists."""

    from trading_journal import desktop

    monkeypatch.setattr(desktop, "_os_lock_functions", lambda: None)


def test_desktop_pid_file_lock_discards_a_legacy_lock_when_its_pid_was_reused(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    _force_pid_file_lock(monkeypatch)
    lock_path = tmp_path / "desktop.lock"
    lock_path.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(desktop, "_process_is_running", lambda process_id: process_id == 12345)
    monkeypatch.setattr(desktop, "_process_looks_like_desktop", lambda _process_id: False)

    lock = DesktopInstanceLock(lock_path)
    lock.acquire()

    assert ":" in lock_path.read_text(encoding="utf-8")
    lock.release()
    assert not lock_path.exists()


def test_desktop_pid_file_lock_keeps_a_current_process_identity(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    _force_pid_file_lock(monkeypatch)
    lock_path = tmp_path / "desktop.lock"
    lock_path.write_text("12345:linux:stable-start-time", encoding="utf-8")
    monkeypatch.setattr(desktop, "_process_is_running", lambda process_id: process_id == 12345)
    monkeypatch.setattr(desktop, "_process_start_identity", lambda process_id: "linux:stable-start-time" if process_id == 12345 else None)

    with pytest.raises(desktop.DesktopAlreadyRunningError, match="already running"):
        DesktopInstanceLock(lock_path).acquire()


def test_process_start_identity_uses_the_win32_creation_time(monkeypatch) -> None:
    """Returning None here would make the PID-file lock treat live owners as stale."""

    import ctypes

    from trading_journal import desktop

    class FakeKernel32:
        def OpenProcess(self, _access, _inherit, _process_id):
            return 1

        def GetProcessTimes(self, _handle, creation_ref, _exit_ref, _kernel_ref, _user_ref):
            creation_ref._obj.dwHighDateTime = 31
            creation_ref._obj.dwLowDateTime = 4242
            return 1

        def CloseHandle(self, _handle):
            return None

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=FakeKernel32()), raising=False)

    assert desktop._process_start_identity(4242, platform="win32") == "windows:31:4242"


def test_process_start_identity_is_present_for_a_live_local_process() -> None:
    from trading_journal import desktop
    import os

    identity = desktop._process_start_identity(os.getpid())

    if sys.platform.startswith("linux") or sys.platform.startswith("win"):
        assert identity


def test_process_probe_treats_an_invalid_windows_handle_as_not_running(monkeypatch) -> None:
    from trading_journal import desktop

    def invalid_handle(_process_id: int, _signal: int) -> None:
        raise OSError(6, "The handle is invalid")

    monkeypatch.setattr(desktop.os, "kill", invalid_handle)

    assert desktop._process_is_running(12345) is False


def _fake_win32_process_probe(monkeypatch, *, open_succeeds: bool, exit_code: int, get_exit_code_succeeds: bool = True):
    import ctypes

    calls: dict[str, object] = {}

    class FakeKernel32:
        def OpenProcess(self, _access, _inherit, process_id):
            calls["opened_pid"] = process_id
            return 1 if open_succeeds else 0

        def GetExitCodeProcess(self, _handle, exit_code_ref):
            if not get_exit_code_succeeds:
                return 0
            exit_code_ref._obj.value = exit_code
            return 1

        def CloseHandle(self, _handle):
            calls["closed"] = True

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=FakeKernel32()), raising=False)
    return calls


def test_process_probe_uses_win32_liveness_check_when_process_is_still_active(monkeypatch) -> None:
    from trading_journal import desktop

    STILL_ACTIVE = 259
    calls = _fake_win32_process_probe(monkeypatch, open_succeeds=True, exit_code=STILL_ACTIVE)

    assert desktop._process_is_running(4242, platform="win32") is True
    assert calls == {"opened_pid": 4242, "closed": True}


def test_process_probe_uses_win32_liveness_check_when_process_has_exited(monkeypatch) -> None:
    from trading_journal import desktop

    _fake_win32_process_probe(monkeypatch, open_succeeds=True, exit_code=0)

    assert desktop._process_is_running(4242, platform="win32") is False


def test_process_probe_treats_a_failed_open_process_as_not_running_on_windows(monkeypatch) -> None:
    from trading_journal import desktop

    _fake_win32_process_probe(monkeypatch, open_succeeds=False, exit_code=0)

    assert desktop._process_is_running(4242, platform="win32") is False


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

    control.request_show()
    assert control.show_requested() is True
    assert control.consume_show_request() is True
    assert control.consume_show_request() is False
    assert control.shutdown_requested() is False


def test_desktop_runtime_record_round_trips_and_ignores_a_dead_owner(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    record = desktop.DesktopRuntimeRecord(tmp_path / "desktop-runtime.json")
    assert record.read() is None

    record.write(port=18501, url="http://127.0.0.1:18501", process_id=4242)

    monkeypatch.setattr(desktop, "_process_is_running", lambda process_id: process_id == 4242)
    payload = record.read()
    assert payload is not None
    assert payload["url"] == "http://127.0.0.1:18501"
    assert payload["port"] == 18501

    monkeypatch.setattr(desktop, "_process_is_running", lambda _process_id: False)
    assert record.read() is None

    record.clear()
    assert not (tmp_path / "desktop-runtime.json").exists()


def test_desktop_runtime_record_ignores_a_corrupt_file(tmp_path: Path) -> None:
    from trading_journal import desktop

    path = tmp_path / "desktop-runtime.json"
    path.write_text("{not json", encoding="utf-8")

    assert desktop.DesktopRuntimeRecord(path).read() is None


def _headless_desktop_environment(monkeypatch, tmp_path: Path):
    from trading_journal import desktop

    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(tmp_path / "desktop-data"))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_HEADLESS", "1")
    monkeypatch.delenv("TRADING_JOURNAL_DESKTOP_PORT", raising=False)
    monkeypatch.delenv("TRADING_JOURNAL_DB", raising=False)
    return desktop.desktop_runtime_paths()


def test_second_launch_force_closes_the_running_instance_and_takes_over(monkeypatch, tmp_path: Path) -> None:
    """Newest launch wins: a live instance is terminated so this launch owns the lock."""

    from trading_journal import desktop

    paths = _headless_desktop_environment(monkeypatch, tmp_path)
    paths.data_directory.mkdir(parents=True, exist_ok=True)

    owner = _hold_lock_in_subprocess(paths.lock_path)  # a real process holding the OS lock
    record = desktop.DesktopRuntimeRecord(paths.runtime_record_path)
    record.write(port=18501, url="http://127.0.0.1:18501", process_id=owner.pid)
    monkeypatch.setattr(desktop, "_TAKEOVER_TIMEOUT_SECONDS", 10.0)

    contender = DesktopInstanceLock(paths.lock_path)
    try:
        desktop._acquire_lock_taking_over(paths, record, contender)
        # The old instance was force-closed and the contender now owns the lock.
        owner.wait(timeout=10)
        assert owner.poll() is not None
    finally:
        contender.release()
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)


def test_terminate_process_tree_stops_a_running_process(monkeypatch) -> None:
    if sys.platform.startswith("win"):
        pytest.skip("POSIX signal path")
    from trading_journal import desktop

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        desktop._terminate_process_tree(proc.pid)
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_takeover_raises_when_a_live_instance_cannot_be_closed(monkeypatch, tmp_path: Path) -> None:
    from trading_journal import desktop

    paths = _headless_desktop_environment(monkeypatch, tmp_path)
    paths.data_directory.mkdir(parents=True, exist_ok=True)

    owner_lock = DesktopInstanceLock(paths.lock_path)
    owner_lock.acquire()  # held for the whole test; the "kill" below is a no-op
    record = desktop.DesktopRuntimeRecord(paths.runtime_record_path)
    record.write(port=18501, url="http://127.0.0.1:18501", process_id=999999)
    monkeypatch.setattr(desktop, "_process_is_running", lambda _process_id: True)
    monkeypatch.setattr(desktop, "_terminate_process_tree", lambda _process_id: None)
    monkeypatch.setattr(desktop, "_TAKEOVER_TIMEOUT_SECONDS", 0.5)

    contender = DesktopInstanceLock(paths.lock_path)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            desktop._acquire_lock_taking_over(paths, record, contender)
    finally:
        owner_lock.release()


def test_start_desktop_application_reports_when_takeover_fails(monkeypatch, tmp_path: Path) -> None:
    """A failed force-close exits cleanly (code 1), never a traceback popup."""

    from trading_journal import desktop

    paths = _headless_desktop_environment(monkeypatch, tmp_path)
    paths.data_directory.mkdir(parents=True, exist_ok=True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("Trade Compass is already running and could not be closed.")

    monkeypatch.setattr(desktop, "_acquire_lock_taking_over", boom)

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("no child process should start when takeover fails")

    monkeypatch.setattr(desktop.subprocess, "Popen", forbidden_popen)

    assert desktop.start_desktop_application() == 1
    assert "could not be closed" in paths.log_path.read_text(encoding="utf-8")


def test_desktop_window_raises_itself_when_a_second_launch_asks_for_it(tmp_path: Path) -> None:
    from trading_journal import desktop

    calls: list[str] = []

    class FakeWindow:
        def restore(self) -> None:
            calls.append("restore")

        def show(self) -> None:
            calls.append("show")
            raise RuntimeError("a GUI backend failure must not kill the window")

    control = DesktopSyncControl(
        tmp_path / "sync.request", tmp_path / "shutdown.request", show_path=tmp_path / "show.request"
    )
    control.request_show()

    watcher = threading.Thread(
        target=desktop._raise_window_on_show_request,
        args=(FakeWindow(), control),
        kwargs={"poll_seconds": 0.01},
        daemon=True,
    )
    watcher.start()
    for _ in range(200):
        if calls:
            break
        time.sleep(0.01)

    assert calls == ["restore", "show"]
    assert not (tmp_path / "show.request").exists()


class _FakeChild:
    """A child process that has already exited, so the supervisor loop ends."""

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.returncode = 0

    def poll(self) -> int:
        return 0


def _run_supervisor_with_fake_children(monkeypatch, tmp_path: Path, *, healthy_after: int):
    from trading_journal import desktop

    paths = _headless_desktop_environment(monkeypatch, tmp_path)
    paths.data_directory.mkdir(parents=True, exist_ok=True)
    spawned: list[list[str]] = []
    published: list[dict[str, object]] = []
    ports = iter([18501, 18502, 18503, 18504])

    def fake_popen(command, **_kwargs):
        spawned.append(command)
        return _FakeChild(command)

    def capture_record(_self, *, port: int, url: str, process_id: int | None = None) -> None:
        published.append({"port": port, "url": url})

    monkeypatch.setattr(desktop, "_database_is_ready", lambda _path: (False, None))
    monkeypatch.setattr(desktop, "desktop_server_port", lambda: next(ports))
    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(desktop.DesktopRuntimeRecord, "write", capture_record)

    attempts = {"count": 0}

    def wait_for_server(_url, _process, timeout_seconds: float = 30.0) -> bool:
        attempts["count"] += 1
        return attempts["count"] >= healthy_after

    monkeypatch.setattr(desktop, "_wait_for_server", wait_for_server)
    return desktop, paths, spawned, published


def test_supervisor_retries_on_a_fresh_port_when_the_server_cannot_bind(monkeypatch, tmp_path: Path) -> None:
    desktop, paths, spawned, published = _run_supervisor_with_fake_children(monkeypatch, tmp_path, healthy_after=2)

    assert desktop.start_desktop_application() == 0

    server_ports = [command[command.index("--port") + 1] for command in spawned if "--run-server" in command]
    assert server_ports == ["18501", "18502"]
    # A second launch must be pointed at the port that actually came up.
    assert published == [{"port": 18502, "url": "http://127.0.0.1:18502"}]
    assert not paths.runtime_record_path.exists()


def test_supervisor_gives_up_after_repeated_server_start_failures(monkeypatch, tmp_path: Path) -> None:
    desktop, paths, spawned, published = _run_supervisor_with_fake_children(monkeypatch, tmp_path, healthy_after=99)

    with pytest.raises(RuntimeError, match="could not start"):
        desktop.start_desktop_application()

    server_ports = [command[command.index("--port") + 1] for command in spawned if "--run-server" in command]
    assert server_ports == ["18501", "18502", "18503"]
    assert published == []
    # A failed launch must not leave the journal locked for the next attempt.
    lock = DesktopInstanceLock(paths.lock_path)
    lock.acquire()
    lock.release()


def test_supervisor_keeps_the_configured_port_instead_of_retrying(monkeypatch, tmp_path: Path) -> None:
    desktop, _paths, spawned, _published = _run_supervisor_with_fake_children(monkeypatch, tmp_path, healthy_after=99)
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_PORT", "18501")

    with pytest.raises(RuntimeError, match="could not start"):
        desktop.start_desktop_application()

    server_commands = [command for command in spawned if "--run-server" in command]
    assert len(server_commands) == 1


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
