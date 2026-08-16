from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi is only installed via the optional 'ingestion' extra")

from fastapi.testclient import TestClient

from trading_journal.application.multiuser import generate_ingestion_token, hash_ingestion_token, ingestion_tokens_path, user_database_path
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository
from trading_journal.ingestion_api import app

TOKEN = "test-token-for-alice"


def _seed_multiuser_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_DATA_DIR", str(tmp_path))
    tokens_path = ingestion_tokens_path()
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(f"tokens:\n  {hash_ingestion_token(TOKEN)}: alice\n", encoding="utf-8")

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


def _position_row(position_id: str = "9001", **overrides: object) -> dict:
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


def test_ingest_rejects_a_request_without_a_bearer_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post("/ingest", json={"positions": [_position_row()]})

    assert response.status_code == 401


def test_ingest_rejects_an_unknown_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"positions": [_position_row()]},
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_ingest_writes_only_into_the_tokens_own_user_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    bob_token = "test-token-for-bob"
    tokens_path = ingestion_tokens_path()
    tokens_path.write_text(
        f"tokens:\n  {hash_ingestion_token(TOKEN)}: alice\n  {hash_ingestion_token(bob_token)}: bob\n",
        encoding="utf-8",
    )
    bob_database_path = user_database_path("bob")
    bob_database_path.parent.mkdir(parents=True, exist_ok=True)
    bob_repository = SQLiteJournalRepository(bob_database_path)
    bob_repository.initialize()
    bob_repository.close()
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"positions": [_position_row()]},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 1
    assert body["updated_count"] == 0

    alice_repository = SQLiteJournalRepository(user_database_path("alice"))
    trade = alice_repository.get_trade_by_mt5_position("123456", "DemoBroker-Live", "9001")
    assert alice_repository.count_trades() == 1
    alice_repository.close()
    assert trade is not None
    assert trade.net_pnl == "98.00"

    bob_repository = SQLiteJournalRepository(bob_database_path)
    assert bob_repository.count_trades() == 0
    bob_repository.close()


def test_ingest_is_idempotent_on_repeated_pushes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    payload = {"positions": [_position_row()]}
    headers = {"Authorization": f"Bearer {TOKEN}"}

    first = client.post("/ingest", json=payload, headers=headers)
    second = client.post("/ingest", json=payload, headers=headers)

    assert first.json()["created_count"] == 1
    assert second.json()["created_count"] == 0
    assert second.json()["updated_count"] == 1


def test_ingest_rejects_an_unregistered_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"positions": [_position_row(position_id="9002") | {"account_login": "999999"}]},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 422


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
def test_ingest_rejects_a_v5_row_missing_a_required_evidence_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing_field: str
) -> None:
    """Regression test: a v5-claiming row must supply every evidence field.

    Otherwise a dropped/mis-keyed field would silently degrade into an
    outcome-inferred R multiple instead of failing the import - see
    CLAUDE.md's "never infer risk from the trade's outcome".
    """
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    row = _position_row()
    del row[missing_field]

    response = client.post("/ingest", json={"positions": [row]}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "missing required columns" in response.json()["detail"]
    assert missing_field in response.json()["detail"]


def test_ingest_rejects_mixed_schema_versions_in_one_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    rows = [_position_row(position_id="9001"), _position_row(position_id="9002", schema_version=4)]

    response = client.post("/ingest", json={"positions": rows}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "one schema version" in response.json()["detail"]


def test_ingest_rejects_an_unsupported_schema_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    row = _position_row(schema_version=4)

    response = client.post("/ingest", json={"positions": [row]}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "expected 5" in response.json()["detail"]


def test_ingest_rejects_mixed_account_identity_in_one_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    rows = [_position_row(position_id="9001"), _position_row(position_id="9002", account_login="999999")]

    response = client.post("/ingest", json={"positions": rows}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "one account identity" in response.json()["detail"]


def test_ingest_rejects_a_duplicate_position_id_in_one_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    rows = [_position_row(position_id="9001"), _position_row(position_id="9001")]

    response = client.post("/ingest", json={"positions": rows}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "one row per completed position" in response.json()["detail"]


def test_ingest_rejects_a_currency_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    row = _position_row(account_currency="EUR")

    response = client.post("/ingest", json={"positions": [row]}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "currency" in response.json()["detail"]


def test_ingest_rejects_inconsistent_account_balance_in_one_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    rows = [_position_row(position_id="9001"), _position_row(position_id="9002", account_balance="2000.00")]

    response = client.post("/ingest", json={"positions": rows}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422
    assert "one current account balance" in response.json()["detail"]


def test_ingest_rejects_a_non_positive_initial_risk_amount(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    row = _position_row(initial_risk_amount="0")

    response = client.post("/ingest", json={"positions": [row]}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422


def test_ingest_rejects_an_empty_positions_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post("/ingest", json={"positions": []}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422


def test_ingest_persists_null_risk_evidence_as_null_not_a_guessed_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A row that explicitly has no risk evidence must be stored as unknown (NULL),
    never inferred from the trade outcome - see CLAUDE.md's "never infer risk from
    the trade's outcome". The required-evidence-field gate (see the parametrized
    "missing a required evidence field" test above) only checks the field was
    *supplied*; this checks a supplied-but-null value survives import as NULL.
    """
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    row = _position_row(
        entry_stop_price=None,
        close_stop_price=None,
        initial_risk_amount=None,
        initial_reward_amount=None,
        pretrade_account_balance=None,
        gross_pnl="-50.00",
        net_pnl="-52.00",
    )

    response = client.post("/ingest", json={"positions": [row]}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    connection = sqlite3.connect(user_database_path("alice"))
    try:
        stored = connection.execute(
            "SELECT entry_stop_price, close_stop_price, initial_risk_amount, initial_reward_amount, pretrade_account_balance "
            "FROM trades WHERE mt5_position_id = '9001'"
        ).fetchone()
    finally:
        connection.close()
    assert stored == (None, None, None, None, None)


def test_ingest_persists_a_nonzero_server_utc_offset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_multiuser_environment(monkeypatch, tmp_path)
    client = TestClient(app)
    row = _position_row(server_utc_offset_minutes=180)

    response = client.post("/ingest", json={"positions": [row]}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    repository = SQLiteJournalRepository(user_database_path("alice"))
    account = next(item for item in repository.list_mt5_accounts() if item.login == "123456")
    repository.close()
    assert account.latest_server_utc_offset_minutes == 180
