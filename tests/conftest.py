"""Shared pytest fixtures for the Trade Compass test suite."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

# Streamlit's AppTest.run() defaults to a ~3s timeout. On cold/loaded CI runners
# (notably Windows) a full app render — importing streamlit/pandas/pyarrow and
# building the page — can exceed that and flake, unrelated to the code under
# test. Raise the default suite-wide; explicit per-call timeouts still win, and
# fast local runs are unaffected since they finish well under the ceiling.
_DEFAULT_APPTEST_TIMEOUT = 30.0


@pytest.fixture(autouse=True)
def _generous_apptest_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    original_run = AppTest.run

    def run(self: AppTest, *, timeout: float | None = None) -> AppTest:
        return original_run(self, timeout=_DEFAULT_APPTEST_TIMEOUT if timeout is None else timeout)

    monkeypatch.setattr(AppTest, "run", run)
