"""BDD-style behavior tests for the maintained MT5 ingestion API.

These tests call the endpoint functions directly. FastAPI owns HTTP transport
serialization; this suite owns Trade Compass authentication, validation, and
persistence behavior.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi is only installed via the optional 'ingestion' extra")

from fastapi import HTTPException

from trading_journal.application.multiuser import (
    hash_ingestion_token,
    ingestion_tokens_path,
    user_database_path,
    users_config_path,
)
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository
from trading_journal.ingestion_api import IngestLivePositionsRequest, IngestRequest, ingest, ingest_live_positions

pytestmark = pytest.mark.bdd

TOKEN = "test-token-for-alice"
AUTHORIZATION = f"Bearer {TOKEN}"


@pytest.fixture
def alice_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Given a registered web user, ingestion token, account, and fresh database."""
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_DATA_DIR", str(tmp_path))
    tokens_path = ingestion_tokens_path()
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(f"tokens:\n  {hash_ingestion_token(TOKEN)}: alice\n", encoding="utf-8")
    users_config_path().write_text("credentials:\n  usernames:\n    alice: {}\n", encoding="utf-8")

    database_path = user_database_path("alice")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.configure_journal(reporting_time_basis="utc")
    repository.register_mt5_account(
        display_name="Alice's account",
        login="123456",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    repository.close()
    return database_path


def position_row(position_id: str = "9001", **overrides: object) -> dict:
    row = {
        "schema_version": 5,
        "account_login": "123456",
        "broker_server": "DemoBroker-Live",
        "account_currency": "USD",
        "position_id": position_id,
        "symbol": "EURUSD",
        "direction": "long",
        "entry_time": "2026-08-10T08:00:00+00:00",
        "exit_time": "2026-08-10T10:00:00+00:00",
        "server_utc_offset_minutes": 0,
        "entry_price": "1.10000",
        "exit_price": "1.10100",
        "volume": "1.00",
        "gross_pnl": "100.00",
        "commission": "-1.50",
        "swap": "-0.25",
        "fees": "-0.25",
        "net_pnl": "98.00",
        "entry_stop_price": "1.09500",
        "entry_target_price": "1.11000",
        "close_stop_price": "1.09800",
        "entry_magic_number": "10001",
        "entry_deal_count": 1,
        "exit_reason": "take_profit",
        "initial_risk_amount": "50.00",
        "initial_reward_amount": "200.00",
        "account_balance": "1000.00",
        "pretrade_account_balance": "900.00",
    }
    row.update(overrides)
    return row


def ingest_positions(*rows: dict, authorization: str | None = AUTHORIZATION):
    return ingest(IngestRequest(positions=list(rows)), authorization=authorization)


def rejection_detail(*rows: dict, authorization: str | None = AUTHORIZATION) -> str:
    with pytest.raises(HTTPException) as raised:
        ingest_positions(*rows, authorization=authorization)
    assert raised.value.status_code == 422
    return str(raised.value.detail)


class TestAuthentication:
    def test_given_no_token_when_ingesting_then_access_is_denied(self, alice_environment: Path) -> None:
        with pytest.raises(HTTPException) as raised:
            ingest_positions(position_row(), authorization=None)

        assert raised.value.status_code == 401

    def test_given_an_unknown_token_when_ingesting_then_access_is_denied(self, alice_environment: Path) -> None:
        with pytest.raises(HTTPException) as raised:
            ingest_positions(position_row(), authorization="Bearer wrong-token")

        assert raised.value.status_code == 401


class TestCompletedPositionIngestion:
    def test_given_a_user_token_when_ingesting_then_only_that_users_database_changes(
        self, alice_environment: Path
    ) -> None:
        bob_database = user_database_path("bob")
        bob_database.parent.mkdir(parents=True, exist_ok=True)
        bob_repository = SQLiteJournalRepository(bob_database)
        bob_repository.initialize()
        bob_repository.close()

        result = ingest_positions(position_row())

        alice_repository = SQLiteJournalRepository(alice_environment)
        trade = alice_repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001")
        bob_repository = SQLiteJournalRepository(bob_database)
        assert (result.created_count, result.updated_count) == (1, 0)
        assert trade is not None and trade.net_pnl == "98.00"
        assert bob_repository.count_trades() == 0
        alice_repository.close()
        bob_repository.close()

    def test_given_a_previous_push_when_repeated_then_the_import_is_idempotent(self, alice_environment: Path) -> None:
        first = ingest_positions(position_row())
        second = ingest_positions(position_row())

        assert (first.created_count, first.updated_count) == (1, 0)
        assert (second.created_count, second.updated_count) == (0, 1)

    @pytest.mark.parametrize(
        "missing_field",
        [
            "entry_stop_price",
            "entry_target_price",
            "close_stop_price",
            "entry_magic_number",
            "entry_deal_count",
            "exit_reason",
            "initial_risk_amount",
            "initial_reward_amount",
            "pretrade_account_balance",
            "server_utc_offset_minutes",
        ],
    )
    def test_given_incomplete_current_evidence_when_ingesting_then_the_row_is_rejected(
        self, alice_environment: Path, missing_field: str
    ) -> None:
        row = position_row()
        del row[missing_field]

        detail = rejection_detail(row)

        assert "missing required columns" in detail
        assert missing_field in detail

    @pytest.mark.parametrize(
        ("rows", "message"),
        [
            ((position_row("1"), position_row("2", schema_version=999)), "one schema version"),
            ((position_row("1", schema_version=999),), "expected 5"),
            ((position_row("1"), position_row("2", account_login="999999")), "one account identity"),
            ((position_row("1"), position_row("1")), "one row per completed position"),
            ((position_row("1", account_currency="EUR"),), "currency"),
            ((position_row("1"), position_row("2", account_balance="2000.00")), "one current account balance"),
            ((), "contains no completed positions"),
        ],
    )
    def test_given_an_invalid_current_batch_when_ingesting_then_it_is_rejected(
        self, alice_environment: Path, rows: tuple[dict, ...], message: str
    ) -> None:
        assert message in rejection_detail(*rows)

    def test_given_unknown_risk_evidence_when_ingesting_then_no_risk_is_guessed(
        self, alice_environment: Path
    ) -> None:
        row = position_row(
            entry_stop_price=None,
            close_stop_price=None,
            initial_risk_amount=None,
            initial_reward_amount=None,
            pretrade_account_balance=None,
            gross_pnl="-50.00",
            net_pnl="-52.00",
        )

        ingest_positions(row)

        connection = sqlite3.connect(alice_environment)
        try:
            stored = connection.execute(
                "SELECT entry_stop_price, close_stop_price, initial_risk_amount, "
                "initial_reward_amount, pretrade_account_balance FROM trades "
                "WHERE mt5_position_id = '9001'"
            ).fetchone()
        finally:
            connection.close()
        assert stored == (None, None, None, None, None)

    def test_given_an_unregistered_account_when_rejected_then_safe_context_is_logged(
        self, alice_environment: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="trading_journal.ingestion_api"):
            detail = rejection_detail(position_row(account_login="999999"))

        assert "MT5 account is not registered or is inactive" in detail
        assert "endpoint=/ingest" in caplog.text
        assert "account_login='999999'" in caplog.text
        assert "broker_server='DemoBroker-Live'" in caplog.text
        assert TOKEN not in caplog.text


class TestLivePositionIngestion:
    def test_given_a_current_snapshot_when_ingesting_then_live_positions_are_replaced(
        self, alice_environment: Path
    ) -> None:
        snapshot = {
            "schema_version": 1,
            "account_login": "123456",
            "broker_server": "DemoBroker-Live",
            "account_currency": "USD",
            "snapshot_time": "2026-08-18T08:00:00+00:00",
            "positions": [
                {
                    "schema_version": 1,
                    "account_login": "123456",
                    "broker_server": "DemoBroker-Live",
                    "account_currency": "USD",
                    "snapshot_time": "2026-08-18T08:00:00+00:00",
                    "position_id": "live-1",
                    "symbol": "EURUSD",
                    "direction": "long",
                    "entry_time": "2026-08-18T07:00:00+00:00",
                    "entry_price": "1.1",
                    "current_price": "1.101",
                    "volume": "1",
                    "stop_price": "1.095",
                    "target_price": "1.11",
                    "net_unrealized_pnl": "10",
                    "risk_to_stop_amount": "50",
                    "magic_number": "10001",
                }
            ],
        }

        result = ingest_live_positions(IngestLivePositionsRequest(snapshot=snapshot), authorization=AUTHORIZATION)

        repository = SQLiteJournalRepository(alice_environment)
        account = repository.get_active_mt5_account()
        assert account is not None
        assert result == {"account_id": account.id}
        assert [row.position_id for row in repository.list_live_positions(account.id)] == ["live-1"]
        repository.close()

    def test_given_an_invalid_snapshot_when_ingesting_then_safe_context_is_logged(
        self, alice_environment: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        snapshot = {
            "schema_version": 1,
            "account_login": "999999",
            "broker_server": "WrongBroker-Live",
            "account_currency": "USD",
            "snapshot_time": "2026-08-18T08:00:00+00:00",
            "positions": [],
        }

        with caplog.at_level(logging.WARNING, logger="trading_journal.ingestion_api"):
            with pytest.raises(HTTPException) as raised:
                ingest_live_positions(IngestLivePositionsRequest(snapshot=snapshot), authorization=AUTHORIZATION)

        assert raised.value.status_code == 422
        assert "account_login='999999'" in caplog.text
        assert "broker_server='WrongBroker-Live'" in caplog.text
        assert TOKEN not in caplog.text
