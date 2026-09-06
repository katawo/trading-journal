"""FastAPI ingestion endpoint: the MT5 EA pushes closed positions here directly,
instead of (or in addition to) writing a local CSV for the Streamlit app to read.

Optional dependency - install with the "ingestion" extra. Run locally with:
    .venv/bin/uvicorn trading_journal.ingestion_api:app --host 127.0.0.1 --port 8600
In production a reverse proxy (e.g. Caddy) terminates HTTPS in front of this;
see /home/thang/.claude/plans/which-free-server-platform-whimsical-sky.md.

Each bearer token belongs to exactly one multi-user account (see
scripts/add_ingestion_token.py) and writes only into that account's own
SQLite file - the same isolation the Streamlit login gate enforces.
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError

from trading_journal.application.import_mt5 import MT5ImportService
from trading_journal.application.live_positions import LivePositionImportService
from trading_journal.application.multiuser import resolve_username_for_token, user_database_path
from trading_journal.domain.errors import ImportValidationError
from trading_journal.domain.models import ImportResult
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository

app = FastAPI(title="Trade Compass ingestion", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)

# One repository per user for the life of the process. initialize() runs the full
# schema-migration sequence inside a write transaction; at one live snapshot every
# ten seconds per connected user, doing that per request is pure overhead and
# serialises against concurrent imports. SQLAlchemy's engine pool is thread-safe,
# so a cached repository is safe to share across FastAPI's worker threads.
#
# Keyed by resolved database path rather than username: production has one path per
# user for the life of the process either way, but tests reuse the same username
# across cases while pointing it at a fresh per-test database (see
# tests/test_ingestion_api.py's alice_environment fixture) - keying by username would
# silently hand a later test the previous test's cached repository.
_repositories: dict[Path, SQLiteJournalRepository] = {}
_repositories_lock = Lock()


def _user_repository(username: str) -> SQLiteJournalRepository:
    database_path = user_database_path(username)
    with _repositories_lock:
        repository = _repositories.get(database_path)
        if repository is None:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            repository = SQLiteJournalRepository(database_path)
            repository.initialize()
            _repositories[database_path] = repository
        return repository


class IngestRequest(BaseModel):
    positions: list[dict]


class IngestLivePositionsRequest(BaseModel):
    snapshot: dict


def _authenticated_username(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    username = resolve_username_for_token(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return username


def _log_validation_rejection(*, endpoint: str, username: str, payload: dict, item_count: int, error: Exception) -> None:
    """Log enough request metadata to diagnose a 422 without exposing trade data or credentials."""
    logger.warning(
        "MT5 ingestion rejected: endpoint=%s user=%r account_login=%r broker_server=%r "
        "account_currency=%r item_count=%d reason=%r",
        endpoint,
        username,
        payload.get("account_login"),
        payload.get("broker_server"),
        payload.get("account_currency"),
        item_count,
        str(error),
    )


@app.post("/ingest", response_model=ImportResult)
def ingest(request: IngestRequest, authorization: str | None = Header(default=None)) -> ImportResult:
    username = _authenticated_username(authorization)
    repository = _user_repository(username)
    try:
        return MT5ImportService(repository).import_json_positions(request.positions, source_label=f"http:{username}")
    except ImportValidationError as error:
        first_position = request.positions[0] if request.positions else {}
        _log_validation_rejection(
            endpoint="/ingest",
            username=username,
            payload=first_position,
            item_count=len(request.positions),
            error=error,
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        # E.g. the account was deactivated between token resolution and the write.
        first_position = request.positions[0] if request.positions else {}
        _log_validation_rejection(
            endpoint="/ingest",
            username=username,
            payload=first_position,
            item_count=len(request.positions),
            error=error,
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, OperationalError) as error:
        # Two overlapping pushes for the same account can both pass the upsert's
        # lookup before either commits; unlike the local path's single-writer
        # guarantee, this endpoint can be hit concurrently - surface it as a
        # retryable conflict rather than a raw 500.
        raise HTTPException(status_code=409, detail="Conflicting concurrent import for this account, retry") from error


@app.post("/ingest/live-positions")
def ingest_live_positions(request: IngestLivePositionsRequest, authorization: str | None = Header(default=None)) -> dict[str, int]:
    username = _authenticated_username(authorization)
    repository = _user_repository(username)
    try:
        account_id = LivePositionImportService(repository).import_snapshot(request.snapshot)
        return {"account_id": account_id}
    except (ImportValidationError, ValueError) as error:
        positions = request.snapshot.get("positions")
        _log_validation_rejection(
            endpoint="/ingest/live-positions",
            username=username,
            payload=request.snapshot,
            item_count=len(positions) if isinstance(positions, list) else 0,
            error=error,
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, OperationalError) as error:
        raise HTTPException(status_code=409, detail="Conflicting concurrent live snapshot, retry") from error


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
