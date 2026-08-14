"""Portable desktop runtime for the local-first Trade Compass.

The Streamlit UI stays unchanged as a browser-rendered interface, but this
module makes it a desktop application: it owns a local-only server, a
background MT5 import worker, and a per-user application-data directory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

from trading_journal.application.auto_sync import MT5AutoSyncResult, MT5AutoSyncService
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


APPLICATION_NAME = "TradingJournal"
"""Stable data-directory key. Never rename this — it determines where an existing
desktop install's local database lives (see desktop_data_directory()). The
user-visible app name is DISPLAY_NAME below and can change freely."""
DISPLAY_NAME = "Trade Compass"
DESKTOP_MODE_ENVIRONMENT_KEY = "TRADING_JOURNAL_DESKTOP_MODE"
DESKTOP_DATA_DIRECTORY_ENVIRONMENT_KEY = "TRADING_JOURNAL_DESKTOP_DATA_DIR"
DESKTOP_PORT_ENVIRONMENT_KEY = "TRADING_JOURNAL_DESKTOP_PORT"
SYNC_STATUS_ENVIRONMENT_KEY = "TRADING_JOURNAL_SYNC_STATUS"
SYNC_REQUEST_ENVIRONMENT_KEY = "TRADING_JOURNAL_SYNC_REQUEST"
SHUTDOWN_REQUEST_ENVIRONMENT_KEY = "TRADING_JOURNAL_SHUTDOWN_REQUEST"
RESET_REQUEST_ENVIRONMENT_KEY = "TRADING_JOURNAL_RESET_REQUEST"
DESKTOP_BROWSER_FALLBACK_ENVIRONMENT_KEY = "TRADING_JOURNAL_DESKTOP_BROWSER"
DESKTOP_HEADLESS_ENVIRONMENT_KEY = "TRADING_JOURNAL_DESKTOP_HEADLESS"
_DEFAULT_SYNC_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class DesktopRuntimePaths:
    """All mutable desktop state lives outside the application bundle."""

    data_directory: Path
    database_path: Path
    sync_status_path: Path
    sync_request_path: Path
    shutdown_request_path: Path
    reset_request_path: Path
    lock_path: Path
    log_path: Path


def desktop_data_directory(
    *,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    """Return the platform-standard user-data directory without creating it."""

    env = os.environ if environment is None else environment
    configured = env.get(DESKTOP_DATA_DIRECTORY_ENVIRONMENT_KEY, "").strip()
    if configured:
        return Path(configured).expanduser()

    resolved_home = home or Path.home()
    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform.startswith("win"):
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / APPLICATION_NAME
        return resolved_home / "AppData" / "Local" / APPLICATION_NAME

    xdg_data_home = env.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / "trading-journal"
    return resolved_home / ".local" / "share" / "trading-journal"


def desktop_runtime_paths(
    *,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> DesktopRuntimePaths:
    """Resolve the durable paths used by the desktop launcher and worker."""

    env = os.environ if environment is None else environment
    data_directory = desktop_data_directory(environment=env, home=home, platform=platform)
    database_path = Path(env.get("TRADING_JOURNAL_DB", str(data_directory / "trading_journal.db"))).expanduser()
    return DesktopRuntimePaths(
        data_directory=data_directory,
        database_path=database_path,
        sync_status_path=Path(env.get(SYNC_STATUS_ENVIRONMENT_KEY, str(data_directory / "mt5-sync-status.json"))).expanduser(),
        sync_request_path=Path(env.get(SYNC_REQUEST_ENVIRONMENT_KEY, str(data_directory / "mt5-sync.request"))).expanduser(),
        shutdown_request_path=Path(env.get(SHUTDOWN_REQUEST_ENVIRONMENT_KEY, str(data_directory / "shutdown.request"))).expanduser(),
        reset_request_path=Path(env.get(RESET_REQUEST_ENVIRONMENT_KEY, str(data_directory / "reset.request"))).expanduser(),
        lock_path=data_directory / "desktop.lock",
        log_path=data_directory / "desktop.log",
    )


def ensure_desktop_runtime_paths(paths: DesktopRuntimePaths) -> None:
    paths.data_directory.mkdir(parents=True, exist_ok=True)
    paths.database_path.parent.mkdir(parents=True, exist_ok=True)


def application_resource_root() -> Path:
    """Locate bundled resources in PyInstaller and source-checkout execution."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def application_entrypoint() -> Path:
    return application_resource_root() / "app.py"


def is_desktop_mode(environment: dict[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return env.get(DESKTOP_MODE_ENVIRONMENT_KEY) == "1"


def desktop_window_enabled(
    *,
    environment: dict[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """Use the native, address-bar-free window on Windows."""

    env = os.environ if environment is None else environment
    resolved_platform = sys.platform if platform is None else platform
    return (
        env.get(DESKTOP_HEADLESS_ENVIRONMENT_KEY) != "1"
        and resolved_platform.startswith("win")
        and env.get(DESKTOP_BROWSER_FALLBACK_ENVIRONMENT_KEY) != "1"
    )


def desktop_headless(environment: dict[str, str] | None = None) -> bool:
    """Return whether this launch must not open a desktop UI."""

    env = os.environ if environment is None else environment
    return env.get(DESKTOP_HEADLESS_ENVIRONMENT_KEY) == "1"


def _database_is_ready(database_path: Path) -> tuple[bool, Exception | None]:
    """Check the database schema without retaining a SQLite file handle."""

    repository = SQLiteJournalRepository(database_path)
    try:
        repository.initialize()
    except Exception as error:
        return False, error
    finally:
        repository.close()
    return True, None


def desktop_environment(paths: DesktopRuntimePaths) -> dict[str, str]:
    """Build the child-process environment shared by server and sync worker."""

    environment = os.environ.copy()
    environment.update(
        {
            DESKTOP_MODE_ENVIRONMENT_KEY: "1",
            DESKTOP_DATA_DIRECTORY_ENVIRONMENT_KEY: str(paths.data_directory),
            "TRADING_JOURNAL_DB": str(paths.database_path),
            SYNC_STATUS_ENVIRONMENT_KEY: str(paths.sync_status_path),
            SYNC_REQUEST_ENVIRONMENT_KEY: str(paths.sync_request_path),
            SHUTDOWN_REQUEST_ENVIRONMENT_KEY: str(paths.shutdown_request_path),
            RESET_REQUEST_ENVIRONMENT_KEY: str(paths.reset_request_path),
        }
    )
    return environment


def _serialize_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _deserialize_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _result_to_payload(result: MT5AutoSyncResult) -> dict[str, Any]:
    return {
        "account_name": result.account_name,
        "account_login": result.account_login,
        "broker_server": result.broker_server,
        "source_path": result.source_path,
        "status": result.status,
        "message": result.message,
        "created_count": result.created_count,
        "updated_count": result.updated_count,
        "export_updated_at": _serialize_datetime(result.export_updated_at),
    }


def _payload_to_result(payload: object) -> MT5AutoSyncResult | None:
    if not isinstance(payload, dict):
        return None
    required = ("account_name", "account_login", "broker_server", "source_path", "status")
    if any(not isinstance(payload.get(name), str) for name in required):
        return None
    return MT5AutoSyncResult(
        account_name=payload["account_name"],
        account_login=payload["account_login"],
        broker_server=payload["broker_server"],
        source_path=payload["source_path"],
        status=payload["status"],
        message=payload.get("message") if isinstance(payload.get("message"), str) else None,
        created_count=payload.get("created_count") if isinstance(payload.get("created_count"), int) else 0,
        updated_count=payload.get("updated_count") if isinstance(payload.get("updated_count"), int) else 0,
        export_updated_at=_deserialize_datetime(payload.get("export_updated_at")),
    )


class DesktopSyncStatusStore:
    """Atomic hand-off of worker state to the Streamlit process.

    A JSON status file avoids a database migration solely for operational UI
    state and lets the worker report failures even when the database cannot be
    opened.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def results(self) -> list[MT5AutoSyncResult]:
        rows = self.read().get("results", [])
        if not isinstance(rows, list):
            return []
        return [result for row in rows if (result := _payload_to_result(row)) is not None]

    def last_import_at(self) -> datetime | None:
        return _deserialize_datetime(self.read().get("last_import_at"))

    def worker_error(self) -> str | None:
        value = self.read().get("worker_error")
        return value if isinstance(value, str) and value else None

    def write(self, results: list[MT5AutoSyncResult], *, worker_error: str | None = None) -> None:
        previous = self.read()
        imported = [result for result in results if result.status == "imported"]
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "checked_at": now.isoformat(),
            "results": [_result_to_payload(result) for result in results],
            "worker_error": worker_error,
            "last_import_at": previous.get("last_import_at"),
            "last_import_created_count": previous.get("last_import_created_count", 0),
            "last_import_updated_count": previous.get("last_import_updated_count", 0),
        }
        if imported:
            payload.update(
                {
                    "last_import_at": now.isoformat(),
                    "last_import_created_count": sum(result.created_count for result in imported),
                    "last_import_updated_count": sum(result.updated_count for result in imported),
                }
            )
        self._atomic_write(payload)

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary_path.replace(self._path)


class DesktopSyncControl:
    """Small file-based signals between the browser UI and the desktop supervisor."""

    def __init__(self, request_path: Path, shutdown_path: Path, reset_path: Path | None = None) -> None:
        self._request_path = request_path
        self._shutdown_path = shutdown_path
        self._reset_path = reset_path or shutdown_path.with_name("reset.request")

    def request_sync(self) -> None:
        self._write_request(self._request_path)

    def request_shutdown(self) -> None:
        self._write_request(self._shutdown_path)

    def request_reset(self) -> None:
        self._write_request(self._reset_path)

    def consume_sync_request(self) -> bool:
        return self._consume_request(self._request_path)

    def shutdown_requested(self) -> bool:
        return self._shutdown_path.is_file()

    def clear_shutdown_request(self) -> None:
        self._clear_request(self._shutdown_path)

    def consume_reset_request(self) -> bool:
        return self._consume_request(self._reset_path)

    def clear_reset_request(self) -> None:
        self._clear_request(self._reset_path)

    @staticmethod
    def _write_request(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        temporary_path.replace(path)

    @staticmethod
    def _consume_request(path: Path) -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _clear_request(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class DesktopSyncWorker:
    """Owns automatic MT5 imports while the desktop launcher is alive."""

    def __init__(self, database_path: Path, status_store: DesktopSyncStatusStore, control: DesktopSyncControl) -> None:
        self._database_path = database_path
        self._status_store = status_store
        self._control = control
        self._repository: SQLiteJournalRepository | None = None

    def sync_once(self) -> list[MT5AutoSyncResult]:
        if self._repository is None:
            self._repository = SQLiteJournalRepository(self._database_path)
            self._repository.initialize()
        results = MT5AutoSyncService(self._repository).sync_configured_exports()
        self._status_store.write(results)
        return results

    def run(self, *, interval_seconds: float = _DEFAULT_SYNC_INTERVAL_SECONDS, parent_process_id: int | None = None) -> None:
        next_sync_at = 0.0
        while True:
            if self._control.shutdown_requested() or (parent_process_id is not None and not _process_is_running(parent_process_id)):
                return
            now = time.monotonic()
            requested = self._control.consume_sync_request()
            if requested or now >= next_sync_at:
                try:
                    self.sync_once()
                except Exception as error:  # Keep the local watcher alive after a transient failure.
                    self._status_store.write([], worker_error=str(error))
                next_sync_at = time.monotonic() + interval_seconds
            time.sleep(min(1.0, max(0.05, next_sync_at - time.monotonic())))


class DesktopInstanceLock:
    """Prevent two desktop supervisors from writing one journal concurrently."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._owned = False

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._clear_stale_lock():
                    continue
                raise RuntimeError("Trade Compass desktop is already running")
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                process_id = os.getpid()
                handle.write(f"{process_id}:{_process_start_identity(process_id) or ''}")
            self._owned = True
            return
        raise RuntimeError("Trade Compass desktop is already running")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        self._owned = False

    def _clear_stale_lock(self) -> bool:
        try:
            value = self._path.read_text(encoding="utf-8").strip()
            process_id_text, separator, recorded_identity = value.partition(":")
            process_id = int(process_id_text)
        except (OSError, ValueError):
            process_id = -1
            separator = ""
            recorded_identity = ""
        current_identity = _process_start_identity(process_id) if process_id > 0 else None
        lock_is_owned = (
            process_id > 0
            and _process_is_running(process_id)
            and (
                (bool(separator) and bool(recorded_identity) and recorded_identity == current_identity)
                or (not separator and _process_looks_like_desktop(process_id))
            )
        )
        if lock_is_owned:
            return False
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        return True


def _process_is_running(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_start_identity(process_id: int) -> str | None:
    """Return a Linux process creation marker to prevent PID-reuse lock errors."""

    if not sys.platform.startswith("linux"):
        return None
    try:
        stat = Path(f"/proc/{process_id}/stat").read_text(encoding="utf-8")
        # The executable name is parenthesized and may contain spaces; field 22
        # (the start time) is therefore the 20th token after its closing bracket.
        return f"linux:{stat.rsplit(')', 1)[1].split()[19]}"
    except (IndexError, OSError):
        return None


def _process_looks_like_desktop(process_id: int) -> bool:
    """Recognize an active legacy PID-only lock on Linux during the upgrade path."""

    if not sys.platform.startswith("linux"):
        return False
    try:
        command = Path(f"/proc/{process_id}/cmdline").read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return False
    return "TradeCompass" in command or "trading_journal.desktop" in command


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socket_handle:
        socket_handle.bind(("127.0.0.1", 0))
        return int(socket_handle.getsockname()[1])


def desktop_server_port(environment: dict[str, str] | None = None) -> int:
    """Use a requested loopback port when a launcher or smoke test needs one."""

    configured = (os.environ if environment is None else environment).get(DESKTOP_PORT_ENVIRONMENT_KEY, "").strip()
    if not configured:
        return _available_loopback_port()
    try:
        port = int(configured)
    except ValueError as error:
        raise RuntimeError(f"{DESKTOP_PORT_ENVIRONMENT_KEY} must be a valid TCP port") from error
    if not 1 <= port <= 65535:
        raise RuntimeError(f"{DESKTOP_PORT_ENVIRONMENT_KEY} must be between 1 and 65535")
    return port


def _child_command(mode: str, *arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, mode, *arguments]
    return [sys.executable, "-m", "trading_journal.desktop", mode, *arguments]


def _wait_for_server(url: str, process: subprocess.Popen[Any], timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urlopen(f"{url}/_stcore/health", timeout=1.0) as response:  # noqa: S310 - loopback only
                if response.status == 200:
                    return True
        except (URLError, TimeoutError):
            time.sleep(0.2)
    return False


def _terminate(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def reset_desktop_database(paths: DesktopRuntimePaths) -> None:
    """Remove only replaceable local journal state after all children stop."""

    for path in (paths.database_path, Path(f"{paths.database_path}-wal"), Path(f"{paths.database_path}-shm")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    for path in (paths.sync_status_path, paths.sync_request_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def run_streamlit_server(port: int) -> None:
    """Run Streamlit in a child process with no externally reachable socket."""

    from streamlit import config
    from streamlit.web import bootstrap

    entrypoint = application_entrypoint()
    if not entrypoint.is_file():
        raise RuntimeError(f"Desktop application resources are incomplete: {entrypoint} is missing")
    # This is the same bootstrap sequence Streamlit's CLI uses, without a
    # Click command context. Calling the private CLI helper directly fails in
    # a packaged child process because no Click context exists there.
    config._main_script_path = str(entrypoint)  # type: ignore[attr-defined]
    flag_options = {
        # Streamlit identifies a frozen executable as a development context
        # unless this is explicit; development mode rejects a fixed local port.
        "global_developmentMode": False,
        "server_address": "127.0.0.1",
        "server_port": port,
        "server_headless": True,
        "browser_gatherUsageStats": False,
        # Portable bundles never reload source. Disabling the watcher avoids
        # expensive recursive watches and Linux inotify limits at startup.
        "server_fileWatcherType": "none",
        "server_runOnSave": False,
    }
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(
        str(entrypoint),
        False,
        [],
        flag_options,
    )


def run_desktop_window(url: str) -> None:
    """Open the local app in a native window instead of an external browser."""

    import webview

    webview.create_window(
        DISPLAY_NAME,
        url,
        width=1440,
        height=920,
        min_size=(1024, 700),
    )
    # pywebview owns the GUI loop and only returns after the user closes the
    # window. It must run in this dedicated child process's main thread.
    webview.start()


def run_sync_worker(
    database_path: Path,
    status_path: Path,
    request_path: Path,
    shutdown_path: Path,
    interval_seconds: float,
    parent_process_id: int | None,
) -> None:
    worker = DesktopSyncWorker(
        database_path,
        DesktopSyncStatusStore(status_path),
        DesktopSyncControl(request_path, shutdown_path),
    )
    worker.run(interval_seconds=interval_seconds, parent_process_id=parent_process_id)


def start_desktop_application() -> int:
    paths = desktop_runtime_paths()
    ensure_desktop_runtime_paths(paths)
    lock = DesktopInstanceLock(paths.lock_path)
    lock.acquire()
    control = DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path, paths.reset_request_path)
    control.clear_shutdown_request()
    control.clear_reset_request()
    environment = desktop_environment(paths)
    log_handle = paths.log_path.open("a", encoding="utf-8")
    window_process: subprocess.Popen[Any] | None = None
    try:
        port = desktop_server_port()
        url = f"http://127.0.0.1:{port}"
        browser_opened = False
        while True:
            server_process: subprocess.Popen[Any] | None = None
            worker_process: subprocess.Popen[Any] | None = None
            try:
                # An incompatible database still gets a local recovery UI, but
                # never starts a worker that could touch the legacy schema.
                database_ready, database_error = _database_is_ready(paths.database_path)
                if database_error is not None:
                    log_handle.write(f"Desktop database startup check failed: {database_error}\n")
                    # Streamlit only executes app.py after a browser websocket
                    # session is established.  Record recovery readiness here so
                    # a headless desktop smoke test can verify the launcher has
                    # selected the reset UI without relying on a browser.
                    log_handle.write("Trade Compass reset recovery screen active.\n")
                    log_handle.flush()
                if database_ready:
                    worker_process = subprocess.Popen(
                        _child_command(
                            "--sync-worker",
                            "--database",
                            str(paths.database_path),
                            "--status",
                            str(paths.sync_status_path),
                            "--request",
                            str(paths.sync_request_path),
                            "--shutdown-request",
                            str(paths.shutdown_request_path),
                            "--parent-pid",
                            str(os.getpid()),
                        ),
                        env=environment,
                        cwd=application_resource_root(),
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                    )
                server_process = subprocess.Popen(
                    _child_command("--run-server", "--port", str(port)),
                    env=environment,
                    cwd=application_resource_root(),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
                if not _wait_for_server(url, server_process):
                    raise RuntimeError(f"Trade Compass could not start. See {paths.log_path}")
                if desktop_window_enabled() and window_process is None:
                    window_process = subprocess.Popen(
                        _child_command("--desktop-window", "--url", url),
                        env=environment,
                        cwd=application_resource_root(),
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                    )
                elif not desktop_headless() and not browser_opened:
                    webbrowser.open(url, new=1)
                    browser_opened = True
                while server_process.poll() is None and not control.shutdown_requested() and not control.consume_reset_request():
                    if window_process is not None and window_process.poll() is not None:
                        if window_process.returncode == 0:
                            return 0
                        log_handle.write("Desktop window could not start; opening the local app in the default browser.\n")
                        log_handle.flush()
                        window_process = None
                        if not browser_opened:
                            webbrowser.open(url, new=1)
                            browser_opened = True
                    time.sleep(0.25)
                if control.shutdown_requested():
                    return 0
                if server_process.poll() is not None:
                    return 0
            finally:
                _terminate(server_process)
                _terminate(worker_process)
            # The supervisor exclusively owns deletion, after both SQLite
            # processes have stopped, then starts a fresh journal at the same URL.
            reset_desktop_database(paths)
    finally:
        _terminate(window_process)
        log_handle.close()
        lock.release()


def self_check() -> int:
    paths = desktop_runtime_paths()
    required = [
        application_entrypoint(),
        application_resource_root() / "app_pages",
        application_resource_root() / "docs" / "three_pillar_framework_guide.md",
        application_resource_root() / "docs" / "three_pillar_framework_guide.vi.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print(json.dumps({"ok": False, "missing": missing}))
        return 1
    ensure_desktop_runtime_paths(paths)
    print(json.dumps({"ok": True, "data_directory": str(paths.data_directory), "entrypoint": str(application_entrypoint())}))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Trade Compass desktop application.")
    parser.add_argument("--run-server", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--desktop-window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sync-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--url", help=argparse.SUPPRESS)
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--status", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--request", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--shutdown-request", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--sync-interval", type=float, default=_DEFAULT_SYNC_INTERVAL_SECONDS, help=argparse.SUPPRESS)
    parser.add_argument("--self-check", action="store_true", help="Validate bundled resources and writable local data paths.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.self_check:
        return self_check()
    if arguments.run_server:
        if arguments.port is None:
            raise SystemExit("--run-server requires --port")
        run_streamlit_server(arguments.port)
        return 0
    if arguments.desktop_window:
        if not arguments.url:
            raise SystemExit("--desktop-window requires --url")
        run_desktop_window(arguments.url)
        return 0
    if arguments.sync_worker:
        required = (arguments.database, arguments.status, arguments.request, arguments.shutdown_request)
        if any(value is None for value in required):
            raise SystemExit("--sync-worker requires desktop runtime paths")
        run_sync_worker(
            arguments.database,
            arguments.status,
            arguments.request,
            arguments.shutdown_request,
            arguments.sync_interval,
            arguments.parent_pid,
        )
        return 0
    return start_desktop_application()


if __name__ == "__main__":
    raise SystemExit(main())
