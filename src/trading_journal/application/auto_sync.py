from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from trading_journal.application.import_mt5 import MT5ImportService
from trading_journal.application.mt5_paths import resolve_account_export_path
from trading_journal.domain.errors import ImportValidationError
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


@dataclass(frozen=True)
class MT5AutoSyncResult:
    account_name: str
    account_login: str
    broker_server: str
    source_path: str
    status: str
    message: str | None = None
    created_count: int = 0
    updated_count: int = 0
    export_updated_at: datetime | None = None


class MT5AutoSyncService:
    """Imports changed, trusted MT5 Common Files exports without any MT5 side effects."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository
        self._import_service = MT5ImportService(repository)

    def sync_configured_exports(self) -> list[MT5AutoSyncResult]:
        results: list[MT5AutoSyncResult] = []
        for account in self._repository.list_mt5_accounts():
            if not account.export_file_path.strip():
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, "", "unconfigured"))
                continue

            path = resolve_account_export_path(account.export_file_path, account.login)
            source_path = str(path)
            if not path.is_file():
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "waiting", "Waiting for the first MT5 export."))
                continue

            try:
                export_updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                message = f"Could not read MT5 export: {error}"
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "failed", message))
                continue

            previous_hash = self._repository.latest_mt5_import_hash(
                login=account.login,
                broker_server=account.broker_server,
                source_file_path=source_path,
            )
            if previous_hash == file_hash:
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "up_to_date", export_updated_at=export_updated_at))
                continue

            try:
                imported = self._import_service.import_csv(path)
            except (ImportValidationError, OSError, RuntimeError) as error:
                message = str(error)
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "failed", message))
                continue

            results.append(
                MT5AutoSyncResult(
                    account.display_name,
                    account.login,
                    account.broker_server,
                    source_path,
                    "imported",
                    created_count=imported.created_count,
                    updated_count=imported.updated_count,
                    export_updated_at=export_updated_at,
                )
            )
        return results
