"""Isolated current-position monitoring; it never creates post-trade records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
from pathlib import Path

from pydantic import ValidationError

from trading_journal.domain.errors import ImportValidationError
from trading_journal.domain.models import MT5LivePositionExport, MT5LiveSnapshotExport, is_valid_protective_stop
from trading_journal.infrastructure.sqlite_repository import LivePositionItem, SQLiteJournalRepository


LIVE_POSITION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LivePositionRisk:
    position: LivePositionItem
    risk_r: Decimal | None
    protected: bool
    risk_amount_available: bool


@dataclass(frozen=True)
class LivePositionReport:
    positions: tuple[LivePositionRisk, ...]
    snapshot_time: datetime | None
    status: str
    total_risk_r: Decimal | None
    limit_r: Decimal | None
    net_unrealized_pnl: Decimal
    unprotected_count: int
    risk_unavailable_count: int
    detail: str


class LivePositionImportService:
    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def import_snapshot(
        self,
        payload: dict,
        *,
        source_file_mtime_ns: int | None = None,
        source_file_size: int | None = None,
    ) -> int:
        try:
            snapshot = MT5LiveSnapshotExport.model_validate(payload)
        except ValidationError as error:
            raise ImportValidationError(f"Invalid MT5 live snapshot: {error.errors()[0]['msg']}") from error
        if snapshot.schema_version != LIVE_POSITION_SCHEMA_VERSION:
            raise ImportValidationError("Unsupported MT5 live snapshot schema version; expected 1")
        seen_ids: set[str] = set()
        for position in snapshot.positions:
            if position.schema_version != LIVE_POSITION_SCHEMA_VERSION:
                raise ImportValidationError("A live snapshot must use one schema version")
            if (
                position.account_login != snapshot.account_login
                or position.broker_server != snapshot.broker_server
                or position.account_currency != snapshot.account_currency
                or position.snapshot_time != snapshot.snapshot_time
            ):
                raise ImportValidationError("Every live position must match the snapshot account and timestamp")
            if position.position_id in seen_ids:
                raise ImportValidationError("A live snapshot must not repeat a position ID")
            seen_ids.add(position.position_id)
        return self._repository.replace_live_positions(
            login=snapshot.account_login,
            broker_server=snapshot.broker_server,
            account_currency=snapshot.account_currency,
            snapshot_time=snapshot.snapshot_time,
            export_interval_seconds=snapshot.export_interval_seconds,
            positions=snapshot.positions,
            source_file_mtime_ns=source_file_mtime_ns,
            source_file_size=source_file_size,
        )

    def import_csv_bytes(
        self,
        export_path: str | Path,
        raw_content: bytes,
        *,
        source_file_mtime_ns: int | None = None,
        source_file_size: int | None = None,
    ) -> int:
        """Read the independent CSV envelope emitted by the resident MT5 EA."""
        del export_path
        try:
            rows = list(csv.DictReader(raw_content.decode("utf-8-sig").splitlines()))
        except UnicodeDecodeError as error:
            raise ImportValidationError("MT5 live export must be UTF-8 CSV") from error
        if not rows:
            raise ImportValidationError("MT5 live export is missing its snapshot metadata row")
        metadata = next((row for row in rows if row.get("record_type") == "snapshot"), None)
        if metadata is None:
            raise ImportValidationError("MT5 live export is missing its snapshot metadata row")
        positions = []
        for row in rows:
            if row.get("record_type") != "position":
                continue
            row.pop("record_type", None)
            row.update({
                "schema_version": metadata.get("schema_version"),
                "account_login": metadata.get("account_login"),
                "broker_server": metadata.get("broker_server"),
                "account_currency": metadata.get("account_currency"),
                "snapshot_time": metadata.get("snapshot_time"),
                "export_interval_seconds": metadata.get("export_interval_seconds"),
            })
            positions.append(row)
        payload = {
            "schema_version": metadata.get("schema_version"),
            "account_login": metadata.get("account_login"),
            "broker_server": metadata.get("broker_server"),
            "account_currency": metadata.get("account_currency"),
            "snapshot_time": metadata.get("snapshot_time"),
            "positions": positions,
        }
        if metadata.get("export_interval_seconds"):
            payload["export_interval_seconds"] = metadata["export_interval_seconds"]
        return self.import_snapshot(
            payload,
            source_file_mtime_ns=source_file_mtime_ns,
            source_file_size=source_file_size,
        )


class LivePositionService:
    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def build_report(self, account_id: int, *, now: datetime | None = None) -> LivePositionReport:
        now = now or datetime.now(timezone.utc)
        rows = self._repository.list_live_positions(account_id)
        snapshot = self._repository.get_live_snapshot(account_id)
        snapshot_time = self._parse_snapshot_time(snapshot.snapshot_time) if snapshot is not None else None
        policy = self._repository.get_active_risk_policy(account_id)
        opening_balance = self._repository.get_account_opening_balance(account_id)
        limit_r = None if policy is None else Decimal(policy.max_open_risk_r)
        standard_r = None
        if policy is not None and opening_balance is not None:
            configured_r = Decimal(opening_balance) * Decimal(policy.standard_risk_per_trade_percent) / Decimal("100")
            if configured_r > 0:
                standard_r = configured_r
        risks = tuple(
            LivePositionRisk(
                position=row,
                risk_r=None if row.risk_to_stop_amount is None or standard_r is None or standard_r <= 0 else Decimal(row.risk_to_stop_amount) / standard_r,
                protected=self._has_protective_stop(row),
                risk_amount_available=row.risk_to_stop_amount is not None,
            )
            for row in rows
        )
        unprotected = sum(not item.protected for item in risks)
        risk_unavailable = sum(item.protected and not item.risk_amount_available for item in risks)
        known_risks = tuple(item.risk_r for item in risks if item.risk_r is not None)
        total = sum(known_risks, Decimal("0"))
        if snapshot is None:
            displayed_total = None
        elif not risks:
            displayed_total = Decimal("0")
        elif known_risks:
            displayed_total = total
        else:
            displayed_total = None
        stale_after = None if snapshot is None else timedelta(seconds=snapshot.export_interval_seconds * 2)
        stale = snapshot_time is not None and stale_after is not None and now - snapshot_time > stale_after
        if snapshot_time is None:
            status, detail = "waiting", "Waiting for the first live MT5 snapshot."
        elif stale:
            status, detail = "stale", "Live snapshot is older than two export intervals."
        elif policy is None or standard_r is None:
            status, detail = "unconfigured", "Set funded capital and an active Risk policy to calculate open risk."
        elif limit_r is not None and total >= limit_r:
            status, detail = "stop", "Known open risk has reached the account limit."
        elif unprotected:
            status, detail = "unprotected", "At least one open position has no valid protective stop; known risk is only a lower bound."
        elif risk_unavailable:
            status, detail = "risk_unavailable", "At least one protected position has unavailable risk calculation; known risk is only a lower bound."
        elif limit_r is not None and total >= limit_r * Decimal("0.8"):
            status, detail = "caution", "Known open risk has reached 80% of the account limit."
        else:
            status, detail = "within", "Known open risk is within the account limit."
        report = LivePositionReport(
            positions=risks,
            snapshot_time=snapshot_time,
            status=status,
            total_risk_r=displayed_total,
            limit_r=limit_r,
            net_unrealized_pnl=sum((Decimal(item.position.net_unrealized_pnl) for item in risks), Decimal("0")),
            unprotected_count=unprotected,
            risk_unavailable_count=risk_unavailable,
            detail=detail,
        )
        self._record_incidents(account_id, report)
        return report

    @staticmethod
    def _parse_snapshot_time(raw: str) -> datetime:
        value = datetime.fromisoformat(raw)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _has_protective_stop(position: LivePositionItem) -> bool:
        if position.stop_price is None:
            return False
        return is_valid_protective_stop(position.direction, Decimal(position.stop_price), Decimal(position.current_price))

    def _record_incidents(self, account_id: int, report: LivePositionReport) -> None:
        # A stale or missing feed cannot safely resolve a previous live-risk event.
        if report.status in {"stale", "waiting", "unconfigured"}:
            return
        active: dict[str, tuple[str, str | None, str]] = {}
        if report.status in {"caution", "stop"}:
            active[f"aggregate:{report.status}"] = (report.status, None, report.detail)
        for item in report.positions:
            if not item.protected:
                active[f"unprotected:{item.position.position_id}"] = ("unprotected", item.position.position_id, "Open position has no valid protective stop.")
            elif not item.risk_amount_available:
                active[f"risk_unavailable:{item.position.position_id}"] = (
                    "risk_unavailable",
                    item.position.position_id,
                    "Protective stop exists, but MT5 could not calculate monetary risk.",
                )
        occurred_at = (report.snapshot_time or datetime.now(timezone.utc)).isoformat()
        self._repository.record_live_incident_transitions(account_id, active, occurred_at=occurred_at)
