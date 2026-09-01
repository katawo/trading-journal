"""Fast BDD smoke coverage for the maintained Streamlit web application."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.bdd


def test_given_a_fresh_journal_when_the_webapp_opens_then_the_dashboard_is_ready(monkeypatch, tmp_path) -> None:
    # Given
    monkeypatch.setenv("TRADING_JOURNAL_DB", str(tmp_path / "journal.db"))

    # When
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)

    # Then
    assert not app.exception
    assert app.title[0].value == "Trade Compass"
    assert [item.value for item in app.subheader] == ["Performance dashboard"]
