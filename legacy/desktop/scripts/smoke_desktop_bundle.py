"""Verify a frozen desktop bundle is self-contained and recovers headlessly."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


_STARTUP_TIMEOUT_SECONDS = 45
_SHUTDOWN_TIMEOUT_SECONDS = 15
_RECOVERY_MARKER = "Trade Compass reset recovery screen active."


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _create_legacy_database(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, monthly_target VARCHAR NOT NULL)")
        connection.commit()
    finally:
        connection.close()


def _failure_details(process: subprocess.Popen[object], log_path: Path) -> str:
    log = "<desktop.log was not created>"
    if log_path.is_file():
        log = log_path.read_text(encoding="utf-8")
    return f"process status: {process.poll()}\ndesktop log:\n{log}"


def _wait_for_recovery(process: subprocess.Popen[object], port: int, log_path: Path) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Desktop recovery exited early\n{_failure_details(process, log_path)}")
        try:
            with urlopen(health_url, timeout=1) as response:  # noqa: S310 - loopback smoke test
                ready = response.status == 200 and log_path.is_file() and _RECOVERY_MARKER in log_path.read_text(encoding="utf-8")
                if ready:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Desktop recovery did not start within {_STARTUP_TIMEOUT_SECONDS} seconds\n{_failure_details(process, log_path)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise SystemExit(f"Desktop executable is missing: {executable}")

    with tempfile.TemporaryDirectory(prefix="trading-journal-smoke-") as directory:
        data_directory = Path(directory)
        subprocess.run([str(executable), "--self-check"], env=os.environ | {"TRADING_JOURNAL_DESKTOP_DATA_DIR": str(data_directory)}, check=True)
        database = data_directory / "trading_journal.db"
        _create_legacy_database(database)
        port = _available_loopback_port()
        environment = os.environ | {
            "TRADING_JOURNAL_DESKTOP_DATA_DIR": str(data_directory),
            "TRADING_JOURNAL_DB": str(database),
            "TRADING_JOURNAL_DESKTOP_PORT": str(port),
            "TRADING_JOURNAL_DESKTOP_HEADLESS": "1",
        }
        process = subprocess.Popen([str(executable)], env=environment)
        try:
            _wait_for_recovery(process, port, data_directory / "desktop.log")
        finally:
            (data_directory / "shutdown.request").write_text("smoke test", encoding="utf-8")
            try:
                process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
