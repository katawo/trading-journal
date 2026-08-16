from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from trading_journal.domain.errors import ImportValidationError
from trading_journal.domain.models import ImportResult, MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


BASE_REQUIRED_COLUMNS = {
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
V5_REQUIRED_COLUMNS = BASE_REQUIRED_COLUMNS | {
    "entry_stop_price",
    "entry_target_price",
    "close_stop_price",
    "entry_magic_number",
    "entry_deal_count",
    "exit_reason",
    "initial_risk_amount",
    "initial_reward_amount",
    "account_balance",
    "pretrade_account_balance",
    "server_utc_offset_minutes",
}
SUPPORTED_SCHEMA_VERSIONS = frozenset({5})


class MT5ImportService:
    """Imports a versioned, local MT5 export without any trading side effects."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def import_csv(self, export_path: str | Path) -> ImportResult:
        path = Path(export_path)
        if not path.is_file():
            raise ImportValidationError(f"Export file does not exist: {path}")

        return self.import_bytes(path, path.read_bytes())

    def import_bytes(
        self,
        export_path: str | Path,
        raw_content: bytes,
        *,
        source_file_mtime_ns: int | None = None,
        source_file_size: int | None = None,
    ) -> ImportResult:
        """Import a previously-read export so auto-sync does not read it twice."""
        path = Path(export_path)
        file_hash = hashlib.sha256(raw_content).hexdigest()
        try:
            rows = list(csv.DictReader(raw_content.decode("utf-8-sig").splitlines()))
        except UnicodeDecodeError as error:
            raise ImportValidationError("MT5 export must be UTF-8 CSV") from error

        if not rows:
            raise ImportValidationError("MT5 export contains no completed positions")
        schema_values = {row.get("schema_version", "").strip() for row in rows}
        if len(schema_values) != 1:
            raise ImportValidationError("An MT5 export must use one schema version")
        try:
            schema_version = int(schema_values.pop())
        except ValueError as error:
            raise ImportValidationError("MT5 export schema version must be a number") from error
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            supported = " or ".join(str(version) for version in sorted(SUPPORTED_SCHEMA_VERSIONS))
            raise ImportValidationError(f"Unsupported MT5 export schema version; expected {supported}")
        required_columns = V5_REQUIRED_COLUMNS
        if not required_columns.issubset(rows[0]):
            missing = ", ".join(sorted(required_columns - set(rows[0])))
            raise ImportValidationError(f"MT5 export is missing required columns: {missing}")

        try:
            positions = [MT5PositionExport.model_validate(row) for row in rows]
        except ValidationError as error:
            raise ImportValidationError(f"Invalid MT5 export row: {error.errors()[0]['msg']}") from error

        return self._import_validated_positions(
            positions,
            source_path=str(path),
            source_hash=file_hash,
            source_file_mtime_ns=source_file_mtime_ns,
            source_file_size=source_file_size,
        )

    def import_json_positions(self, rows: list[dict], *, source_label: str) -> ImportResult:
        """Import already-parsed rows pushed directly by an MT5 EA (no local file).

        Shares every validation/idempotency rule with the CSV path via
        _import_validated_positions, including the required-column check (enforced
        there via model_fields_set, since JSON rows have no shared header row to
        check up front like the CSV path does) - only how the rows first arrive
        differs.
        """
        if not rows:
            raise ImportValidationError("MT5 export contains no completed positions")
        try:
            positions = [MT5PositionExport.model_validate(row) for row in rows]
        except ValidationError as error:
            raise ImportValidationError(f"Invalid MT5 export row: {error.errors()[0]['msg']}") from error

        payload_hash = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return self._import_validated_positions(positions, source_path=source_label, source_hash=payload_hash)

    def _import_validated_positions(
        self,
        positions: list[MT5PositionExport],
        *,
        source_path: str,
        source_hash: str,
        source_file_mtime_ns: int | None = None,
        source_file_size: int | None = None,
    ) -> ImportResult:
        if not positions:
            raise ImportValidationError("MT5 export contains no completed positions")
        schema_versions = {position.schema_version for position in positions}
        if len(schema_versions) != 1:
            raise ImportValidationError("An MT5 export must use one schema version")
        schema_version = schema_versions.pop()
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            supported = " or ".join(str(version) for version in sorted(SUPPORTED_SCHEMA_VERSIONS))
            raise ImportValidationError(f"Unsupported MT5 export schema version; expected {supported}")

        # CSV rows always have every header key present (DictReader), so this is a
        # no-op there; JSON rows have no shared header row, so this is the only
        # guard against a schema_version=5 payload that omits v5 evidence fields -
        # which would otherwise silently degrade into outcome-inferred R multiples.
        for position in positions:
            missing = V5_REQUIRED_COLUMNS - position.model_fields_set
            if missing:
                raise ImportValidationError(f"MT5 export is missing required columns: {', '.join(sorted(missing))}")

        balances = {position.account_balance for position in positions}
        if None in balances:
            raise ImportValidationError("Schema-v5 MT5 export requires an account balance on every position")
        if len(balances) != 1:
            raise ImportValidationError("Schema-v5 MT5 export must use one current account balance")
        live_account_balance = balances.pop()

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
        return self._repository.upsert_mt5_positions(
            account.id,
            positions,
            source_path,
            source_hash,
            live_account_balance=live_account_balance,
            source_file_mtime_ns=source_file_mtime_ns,
            source_file_size=source_file_size,
        )
