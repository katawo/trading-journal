"""Deterministic daily workflow summary for the active trading account."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from decimal import Decimal

from trading_journal.application.framework import (
    REVIEWED_KINDS,
    FrameworkFocusProgress,
    FrameworkService,
)
from trading_journal.application.reporting_time import reporting_date, reporting_datetime
from trading_journal.infrastructure.sqlite_repository import (
    FrameworkFocusView,
    SQLiteJournalRepository,
)


@dataclass(frozen=True)
class TodayIssueSummary:
    code: str
    count: int


@dataclass(frozen=True)
class TodayTradeSummary:
    trade_id: int
    display_label: str
    position_count: int
    symbol: str
    direction: str
    closed_at: str
    net_pnl: str
    review_kind: str
    classification: str | None
    violation_codes: tuple[str, ...]
    hard_rule_codes: tuple[str, ...]
    corrective_action: str | None

    @property
    def reviewed(self) -> bool:
        return self.review_kind in REVIEWED_KINDS


@dataclass(frozen=True)
class TodayOverview:
    report_date: str
    reporting_time_basis: str
    realized_pnl: str
    daily_r: str | None
    trades: tuple[TodayTradeSummary, ...]
    reviewed_count: int
    pending_count: int
    mistakes: tuple[TodayIssueSummary, ...]
    hard_rules: tuple[TodayIssueSummary, ...]
    active_focus: FrameworkFocusView | None
    focus_progress: FrameworkFocusProgress | None
    resolved_focuses: tuple[FrameworkFocusView, ...]


class TodayService:
    """Compose outcome, review, and coaching evidence for one reporting day."""

    def __init__(
        self,
        repository: SQLiteJournalRepository,
        *,
        local_zone: tzinfo | None = None,
        framework: FrameworkService | None = None,
    ) -> None:
        self._repository = repository
        self._local_zone = local_zone
        self._framework = framework or FrameworkService(repository, local_zone=local_zone)

    def build(self, account_id: int, *, now: datetime | None = None) -> TodayOverview:
        current = now or datetime.now(timezone.utc)
        current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
        account = next((item for item in self._repository.list_mt5_accounts() if item.id == account_id), None)
        if account is None:
            raise ValueError("Trading account was not found")
        settings = self._repository.get_journal_settings()
        report_date = self._framework.today(account_id, now=current)
        entries = self._framework.trade_process_evidence(account_id)
        assessments = {
            item.trade_id: item
            for item in self._repository.list_active_post_trade_assessments(account_id)
        }

        daily: list[TodayTradeSummary] = []
        for trade, score in entries:
            if reporting_date(
                trade.exit_time,
                trade.server_utc_offset_minutes,
                settings.reporting_time_basis,
                local_zone=self._local_zone,
            ) != report_date:
                continue
            assessment = assessments.get(trade.id)
            daily.append(
                TodayTradeSummary(
                    trade_id=trade.id,
                    display_label=trade.display_label,
                    position_count=len(trade.position_ids),
                    symbol=trade.symbol,
                    direction=trade.direction,
                    closed_at=reporting_datetime(
                        trade.exit_time,
                        trade.server_utc_offset_minutes,
                        settings.reporting_time_basis,
                        local_zone=self._local_zone,
                    ).isoformat(),
                    net_pnl=trade.net_pnl,
                    review_kind=score.review_kind,
                    classification=score.classification,
                    violation_codes=(
                        tuple(code for code in score.violation_codes if code not in score.hard_rule_codes)
                        if score.review_kind in REVIEWED_KINDS
                        else ()
                    ),
                    hard_rule_codes=score.hard_rule_codes if score.review_kind in REVIEWED_KINDS else (),
                    corrective_action=(
                        assessment.corrective_action
                        if assessment is not None and score.review_kind == "manual_review"
                        else None
                    ),
                )
            )
        daily.sort(key=lambda item: (item.closed_at, item.trade_id), reverse=True)

        mistake_counts = Counter(code for item in daily for code in item.violation_codes)
        hard_rule_counts = Counter(code for item in daily for code in item.hard_rule_codes)
        active_focus, focus_progress = self._framework.focus_progress(account_id)
        offset = account.latest_server_utc_offset_minutes or 0
        resolved_focuses = tuple(
            sorted(
                (
                    focus
                    for focus in self._repository.list_framework_focuses(account_id)
                    if focus.status in {"completed", "abandoned"}
                    and focus.resolved_at is not None
                    and reporting_date(
                        focus.resolved_at,
                        offset,
                        settings.reporting_time_basis,
                        local_zone=self._local_zone,
                    ) == report_date
                ),
                key=lambda focus: (focus.resolved_at or "", focus.id),
                reverse=True,
            )
        )
        risk_snapshot = self._framework.risk_snapshot(account_id, now=current)
        reviewed_count = sum(item.reviewed for item in daily)

        return TodayOverview(
            report_date=report_date.isoformat(),
            reporting_time_basis=settings.reporting_time_basis,
            realized_pnl=str(sum((Decimal(item.net_pnl) for item in daily), Decimal("0"))),
            daily_r=risk_snapshot.daily_r,
            trades=tuple(daily),
            reviewed_count=reviewed_count,
            pending_count=len(daily) - reviewed_count,
            mistakes=tuple(
                TodayIssueSummary(code, count)
                for code, count in sorted(mistake_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            hard_rules=tuple(
                TodayIssueSummary(code, count)
                for code, count in sorted(hard_rule_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            active_focus=active_focus,
            focus_progress=focus_progress,
            resolved_focuses=resolved_focuses,
        )
