from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from trading_journal.application.import_mt5 import MT5ImportService
from trading_journal.application.live_positions import LivePositionImportService
from trading_journal.application.mt5_paths import resolve_account_export_path, resolve_account_live_export_path
from trading_journal.application.multiuser import is_multiuser_mode
from trading_journal.domain.errors import ImportValidationError
from trading_journal.infrastructure.sqlite_repository import AccountListItem, SQLiteJournalRepository


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
    live_status: str | None = None
    live_message: str | None = None
    live_updated_at: datetime | None = None


class MT5AutoSyncService:
    """Imports changed, trusted MT5 Common Files exports without any MT5 side effects."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository
        self._import_service = MT5ImportService(repository)
        self._live_import_service = LivePositionImportService(repository)

    def sync_configured_exports(self) -> list[MT5AutoSyncResult]:
        results: list[MT5AutoSyncResult] = []
        for account in self._repository.list_mt5_accounts():
            if is_multiuser_mode():
                # A multiuser/Docker web process never has access to a local MT5 export file -
                # MT5 runs on a separate host and pushes here via POST /ingest instead - so a
                # configured export_file_path (even the UI's computed default) is meaningless here.
                results.append(self._ingestion_sync_result(account))
                continue

            live_status, live_message, live_updated_at = self._sync_live_export(account)

            if not account.export_file_path.strip():
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, "", "unconfigured", live_status=live_status, live_message=live_message, live_updated_at=live_updated_at))
                continue

            path = resolve_account_export_path(account.export_file_path, account.login)
            source_path = str(path)
            if not path.is_file():
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "waiting", "Waiting for the first MT5 export.", live_status=live_status, live_message=live_message, live_updated_at=live_updated_at))
                continue

            try:
                file_stat = path.stat()
                export_updated_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
                fingerprint = self._repository.latest_mt5_import_fingerprint(
                    login=account.login,
                    broker_server=account.broker_server,
                    source_file_path=source_path,
                )
                if fingerprint is not None and fingerprint[1:] == (file_stat.st_mtime_ns, file_stat.st_size):
                    results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "up_to_date", export_updated_at=export_updated_at, live_status=live_status, live_message=live_message, live_updated_at=live_updated_at))
                    continue
                raw_content = path.read_bytes()
                file_hash = hashlib.sha256(raw_content).hexdigest()
            except OSError as error:
                message = f"Could not read MT5 export: {error}"
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "failed", message, live_status=live_status, live_message=live_message, live_updated_at=live_updated_at))
                continue

            previous_hash = self._repository.latest_mt5_import_hash(
                login=account.login,
                broker_server=account.broker_server,
                source_file_path=source_path,
            )
            if previous_hash == file_hash:
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "up_to_date", export_updated_at=export_updated_at, live_status=live_status, live_message=live_message, live_updated_at=live_updated_at))
                continue

            try:
                imported = self._import_service.import_bytes(
                    path,
                    raw_content,
                    source_file_mtime_ns=file_stat.st_mtime_ns,
                    source_file_size=file_stat.st_size,
                )
            except (ImportValidationError, OSError, RuntimeError) as error:
                message = str(error)
                results.append(MT5AutoSyncResult(account.display_name, account.login, account.broker_server, source_path, "failed", message, live_status=live_status, live_message=live_message, live_updated_at=live_updated_at))
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
                    live_status=live_status,
                    live_message=live_message,
                    live_updated_at=live_updated_at,
                )
            )
        return results

    def _sync_live_export(self, account: AccountListItem) -> tuple[str, str | None, datetime | None]:
        if not account.export_file_path.strip():
            return "unconfigured", None, None
        path = resolve_account_live_export_path(account.export_file_path, account.login)
        if not path.is_file():
            return "waiting", "Waiting for the first live MT5 snapshot.", None
        try:
            file_stat = path.stat()
            updated_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
            prior = self._repository.get_live_snapshot(account.id)
            if prior is not None and (prior.source_file_mtime_ns, prior.source_file_size) == (file_stat.st_mtime_ns, file_stat.st_size):
                return "up_to_date", None, updated_at
            self._live_import_service.import_csv_bytes(
                path,
                path.read_bytes(),
                source_file_mtime_ns=file_stat.st_mtime_ns,
                source_file_size=file_stat.st_size,
            )
        except (ImportValidationError, OSError, RuntimeError, ValueError) as error:
            return "failed", str(error), None
        return "imported", None, updated_at

    def _ingestion_sync_result(self, account: AccountListItem) -> MT5AutoSyncResult:
        """Status for an account fed by the ingestion API (POST /ingest) instead of a local export file."""
        run = self._repository.latest_ingestion_import(account.id)
        live_snapshot = self._repository.get_live_snapshot(account.id)
        live_updated_at = None if live_snapshot is None else datetime.fromisoformat(live_snapshot.source_updated_at)
        live_status = "waiting" if live_snapshot is None else "imported"
        if run is None:
            return MT5AutoSyncResult(
                account.display_name, account.login, account.broker_server,
                "ingestion", "waiting", "Waiting for the first MT5 export.", live_status=live_status,
                live_updated_at=live_updated_at,
            )
        created_at, _created_count, _updated_count = run
        return MT5AutoSyncResult(
            account.display_name, account.login, account.broker_server,
            "ingestion", "up_to_date",
            export_updated_at=datetime.fromisoformat(created_at),
            live_status=live_status,
            live_updated_at=live_updated_at,
        )
