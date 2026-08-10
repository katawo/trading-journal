from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from pydantic import ValidationError

from trading_journal.domain.errors import ImportValidationError
from trading_journal.domain.models import ImportResult, MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


REQUIRED_COLUMNS = {
    "schema_version",
    "account_login",
    "broker_server",
    "account_currency",
    "position_id",
    "symbol",
    "direction",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "volume",
    "gross_pnl",
    "commission",
    "swap",
    "fees",
    "net_pnl",
}


class MT5ImportService:
    """Imports a versioned, local MT5 export without any trading side effects."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def import_csv(self, export_path: str | Path) -> ImportResult:
        path = Path(export_path)
        if not path.is_file():
            raise ImportValidationError(f"Export file does not exist: {path}")

        raw_content = path.read_bytes()
        file_hash = hashlib.sha256(raw_content).hexdigest()
        try:
            rows = list(csv.DictReader(raw_content.decode("utf-8-sig").splitlines()))
        except UnicodeDecodeError as error:
            raise ImportValidationError("MT5 export must be UTF-8 CSV") from error

        if not rows:
            raise ImportValidationError("MT5 export contains no completed positions")
        if not REQUIRED_COLUMNS.issubset(rows[0]):
            missing = ", ".join(sorted(REQUIRED_COLUMNS - set(rows[0])))
            raise ImportValidationError(f"MT5 export is missing required columns: {missing}")

        try:
            positions = [MT5PositionExport.model_validate(row) for row in rows]
        except ValidationError as error:
            raise ImportValidationError(f"Invalid MT5 export row: {error.errors()[0]['msg']}") from error

        identities = {(item.account_login, item.broker_server, item.account_currency) for item in positions}
        if len(identities) != 1:
            raise ImportValidationError("An MT5 export must contain one account identity and currency")
        if len({item.position_id for item in positions}) != len(positions):
            raise ImportValidationError("An MT5 export must contain one row per completed position")

        login, broker_server, currency = identities.pop()
        account = self._repository.find_active_mt5_account(login, broker_server)
        if account is None:
            raise ImportValidationError("MT5 account is not registered or is inactive")
        if account.account_currency != currency:
            raise ImportValidationError("MT5 export currency does not match the registered account currency")
        if self._repository.journal_base_currency() != currency:
            raise ImportValidationError("MT5 export currency does not match the journal base currency")

        return self._repository.upsert_mt5_positions(account.id, positions, str(path), file_hash)
