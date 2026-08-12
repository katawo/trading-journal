"""Verify a frozen desktop bundle serves recovery UI for an old database."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise SystemExit(f"Desktop executable is missing: {executable}")

    with tempfile.TemporaryDirectory(prefix="trading-journal-recovery-") as directory:
        data_directory = Path(directory)
        database = data_directory / "trading_journal.db"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, monthly_target VARCHAR NOT NULL)")

        port = "18501"
        environment = os.environ | {
            "TRADING_JOURNAL_DESKTOP_DATA_DIR": str(data_directory),
            "TRADING_JOURNAL_DB": str(database),
            "TRADING_JOURNAL_DESKTOP_PORT": port,
        }
        process = subprocess.Popen([str(executable)], env=environment)
        health_url = f"http://127.0.0.1:{port}/_stcore/health"
        page_url = f"http://127.0.0.1:{port}/"
        recovery_marker = "Trading Journal reset recovery screen active."
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Desktop recovery exited early with status {process.returncode}")
                try:
                    with urlopen(health_url, timeout=1) as response:  # noqa: S310 - loopback smoke test
                        if response.status != 200:
                            continue
                    with urlopen(page_url, timeout=1) as response:  # noqa: S310 - loopback smoke test
                        if response.status != 200:
                            continue
                    log_path = data_directory / "desktop.log"
                    if log_path.is_file() and recovery_marker in log_path.read_text(encoding="utf-8"):
                        return 0
                except (URLError, TimeoutError):
                    time.sleep(0.2)
            raise RuntimeError("Desktop recovery did not start its local server within 30 seconds")
        finally:
            (data_directory / "shutdown.request").write_text("smoke test", encoding="utf-8")
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
