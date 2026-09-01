"""Archived desktop integration tests.

These tests document the retired desktop behavior and are intentionally outside
the maintained pytest test path. They are not expected to run against the
current web application.
"""

from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest


pytestmark = pytest.mark.desktop


def test_desktop_recovery_can_request_a_reset_for_an_incompatible_database(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE journal_settings (id INTEGER PRIMARY KEY, base_currency VARCHAR(3) NOT NULL, reporting_timezone VARCHAR(64) NOT NULL, monthly_target VARCHAR NOT NULL)"
    )
    connection.commit()
    connection.close()
    data_directory = tmp_path / "desktop-data"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(data_directory))

    app = AppTest.from_file(Path(__file__).parents[3] / "app.py").run()

    assert not app.exception
    assert app.title[0].value == "Trade Compass recovery"
    assert any("predates the greenfield" in item.value for item in app.error)
    assert any(item.label == "Reset local database" for item in app.button)
    confirmation = next(item for item in app.text_input if item.label == "Type RESET to confirm")
    confirmation.set_value("RESET")
    next(item for item in app.button if item.label == "Reset local database").click().run()
    assert not (data_directory / "reset.request").exists()
    assert any("reload automatically" in item.value for item in app.info)


def test_desktop_shows_a_diagnostic_for_a_corrupt_database(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    database_path.write_bytes(b"not a sqlite database")
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(database_path))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")

    app = AppTest.from_file(Path(__file__).parents[3] / "app.py").run()

    assert not app.exception
    assert app.title[0].value == "Trade Compass recovery"
    assert any(item.value == "Trade Compass could not open its local database." for item in app.error)
    assert not any(item.label == "Reset local database" for item in app.button)


def test_desktop_settings_can_request_a_database_reset(monkeypatch, tmp_path):
    data_directory = tmp_path / "desktop-data"
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_MODE", "1")
    monkeypatch.setenv("TRADING_JOURNAL_DESKTOP_DATA_DIR", str(data_directory))

    app = AppTest.from_file(Path(__file__).parents[3] / "app.py").run()
    app.switch_page("app_pages/settings.py").run()

    reset_button = next(item for item in app.button if item.label == "Reset local database")
    assert reset_button.disabled
