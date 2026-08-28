from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, delete, event, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from trading_journal.application.reporting_time import REPORTING_TIME_BASES, normalize_server_timestamp
from trading_journal.domain.models import ImportResult, ImportedTradeView, MT5LivePositionExport, MT5PositionExport
from trading_journal.domain.review_taxonomy import HARD_RULE_CODES, VIOLATION_CODES


_UNSET = object()
ASSESSMENT_GRADES = frozenset({"pass", "partial", "fail"})
MONITORING_RESET_PERIODS = frozenset({"daily", "weekly", "monthly", "all_time"})
PSYCHOLOGY_CRITERIA = (
    "rule_adherence",
    "impulse_control",
    "emotional_control",
    "patience_discipline",
)
RISK_CRITERIA = (
    "policy_adherence",
    "position_size_accuracy",
    "stop_discipline",
    "exposure_limit_compliance",
)
SYSTEM_CRITERIA = (
    "setup_validity",
    "context_alignment",
    "entry_fidelity",
    "invalidation_fidelity",
    "management_exit_fidelity",
)
ASSESSMENT_CRITERIA = PSYCHOLOGY_CRITERIA + RISK_CRITERIA + SYSTEM_CRITERIA


def _decimal_string(value: Decimal | str) -> str:
    return str(Decimal(value))


def _parse_live_snapshot_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def normalize_strategy_name(value: str) -> str:
    return " ".join(value.split()).casefold()


class Base(DeclarativeBase):
    pass


class JournalDatabaseResetRequiredError(RuntimeError):
    """Raised when a database predates the clean three-pillar schema."""


class JournalSettings(Base):
    __tablename__ = "journal_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_time_basis: Mapped[str] = mapped_column(String(16), nullable=False, default="server")
    display_language: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    default_strategy_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_strategy_profile_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=True)
    active_mt5_account_id: Mapped[int | None] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=True)


class MT5Account(Base):
    __tablename__ = "mt5_accounts"
    __table_args__ = (UniqueConstraint("login", name="uq_mt5_account_login"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    login: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_server: Mapped[str] = mapped_column(String(255), nullable=False)
    account_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    opening_balance: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_mt5_balance: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_server_utc_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    export_file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    strategy_profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=False)
    strategy_profile: Mapped["StrategyProfile"] = relationship()
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StrategyProfile(Base):
    __tablename__ = "strategy_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    backtest_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    backtest_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class StrategyMagicNumber(Base):
    __tablename__ = "strategy_magic_numbers"
    __table_args__ = (UniqueConstraint("magic_number", name="uq_strategy_magic_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=False)
    magic_number: Mapped[str] = mapped_column(String(32), nullable=False)


class StrategySetup(Base):
    __tablename__ = "strategy_setups"
    __table_args__ = (UniqueConstraint("strategy_profile_id", "normalized_name", name="uq_strategy_setup_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ReviewContextTag(Base):
    __tablename__ = "review_context_tags"
    __table_args__ = (UniqueConstraint("kind", "normalized_name", name="uq_review_context_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LogicalTrade(Base):
    """A journal trade: one imported position or a user-defined group of positions."""

    __tablename__ = "logical_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    display_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("mt5_account_id", "mt5_position_id", name="uq_mt5_position"),
        Index("ix_trades_account_exit", "mt5_account_id", "exit_time", "id"),
        Index("ix_trades_logical_trade", "logical_trade_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    mt5_account_id: Mapped[int | None] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=True)
    logical_trade_id: Mapped[int] = mapped_column(ForeignKey("logical_trades.id"), nullable=False)
    mt5_position_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[str] = mapped_column(String(64), nullable=False)
    exit_time: Mapped[str] = mapped_column(String(64), nullable=False)
    server_utc_offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[str] = mapped_column(String, nullable=False)
    exit_price: Mapped[str] = mapped_column(String, nullable=False)
    volume: Mapped[str] = mapped_column(String, nullable=False)
    gross_pnl: Mapped[str] = mapped_column(String, nullable=False)
    commission: Mapped[str] = mapped_column(String, nullable=False)
    swap: Mapped[str] = mapped_column(String, nullable=False)
    fees: Mapped[str] = mapped_column(String, nullable=False)
    net_pnl: Mapped[str] = mapped_column(String, nullable=False)
    entry_stop_price: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_target_price: Mapped[str | None] = mapped_column(String, nullable=True)
    close_stop_price: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_magic_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entry_deal_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    initial_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    initial_reward_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    pretrade_account_balance: Mapped[str | None] = mapped_column(String, nullable=True)
    auto_risk_policy_id: Mapped[int | None] = mapped_column(ForeignKey("account_risk_policies.id"), nullable=True)


class LivePosition(Base):
    """Ephemeral current MT5 state; deliberately unrelated to journal trades."""

    __tablename__ = "live_positions"
    __table_args__ = (UniqueConstraint("mt5_account_id", "mt5_position_id", name="uq_live_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    mt5_position_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_time: Mapped[str] = mapped_column(String(64), nullable=False)
    source_updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_price: Mapped[str] = mapped_column(String, nullable=False)
    current_price: Mapped[str] = mapped_column(String, nullable=False)
    volume: Mapped[str] = mapped_column(String, nullable=False)
    stop_price: Mapped[str | None] = mapped_column(String, nullable=True)
    target_price: Mapped[str | None] = mapped_column(String, nullable=True)
    net_unrealized_pnl: Mapped[str] = mapped_column(String, nullable=False)
    risk_to_stop_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    magic_number: Mapped[str | None] = mapped_column(String(32), nullable=True)


class LivePositionSnapshot(Base):
    __tablename__ = "live_position_snapshots"

    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), primary_key=True)
    snapshot_time: Mapped[str] = mapped_column(String(64), nullable=False)
    export_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    source_updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_mtime_ns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LivePositionIncident(Base):
    """An auditable live-risk transition, never post-trade evidence."""

    __tablename__ = "live_position_incidents"
    __table_args__ = (Index("ix_live_incidents_account_key_id", "mt5_account_id", "incident_key", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    incident_key: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    position_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(64), nullable=False)


class MT5ImportRun(Base):
    __tablename__ = "mt5_import_runs"
    __table_args__ = (Index("ix_import_runs_account_path_status_id", "mt5_account_id", "source_file_path", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    source_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_mtime_ns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class AccountRiskPolicy(Base):
    __tablename__ = "account_risk_policies"
    __table_args__ = (UniqueConstraint("mt5_account_id", "version", name="uq_account_risk_policy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_per_trade_percent: Mapped[str] = mapped_column(String, nullable=False)
    maximum_risk_per_trade_percent: Mapped[str] = mapped_column(String, nullable=False)
    daily_loss_limit_r: Mapped[str] = mapped_column(String, nullable=False)
    weekly_loss_limit_r: Mapped[str] = mapped_column(String, nullable=False)
    max_drawdown_percent: Mapped[str] = mapped_column(String, nullable=False)
    drawdown_reset_period: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")
    max_open_risk_r: Mapped[str] = mapped_column(String, nullable=False)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False)
    loss_streak_reset_period: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")
    minimum_rr: Mapped[str] = mapped_column(String, nullable=False)
    correlation_policy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pretrade_balance_auto_evidence_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    server_utc_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class PostTradeAssessment(Base):
    """The single active review of a logical trade — either a one-click auto review or a full manual assessment.

    `method` distinguishes the two: "auto" rows are evidence-driven (neutral Psychology/
    Trading-system defaults; no strategy, note, violations, or hard-rules), "manual" rows
    are a full 13-criterion assessment. Both count toward pillar scores identically once active.
    """

    __tablename__ = "post_trade_assessments"
    __table_args__ = (
        Index(
            "uq_active_post_trade_assessment_logical_trade",
            "logical_trade_id",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
        Index("ix_active_assessments_account", "mt5_account_id", sqlite_where=text("superseded_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    logical_trade_id: Mapped[int] = mapped_column(ForeignKey("logical_trades.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_policy_id: Mapped[int | None] = mapped_column(ForeignKey("account_risk_policies.id"), nullable=True)
    risk_evidence_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_policy_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_profile_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=True)
    strategy_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    setup_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    regime_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    criterion_grades: Mapped[str] = mapped_column(Text, nullable=False)
    violation_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    hard_rule_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    declared_actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    post_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_position_ids: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_trade_label: Mapped[str] = mapped_column(String(160), nullable=False)
    superseded_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PostTradeAssessmentRevision(Base):
    """Immutable copy of a post-trade assessment before it was corrected, upgraded, or superseded."""

    __tablename__ = "post_trade_assessment_revisions"
    __table_args__ = (UniqueConstraint("post_trade_assessment_id", "version", name="uq_post_trade_assessment_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_trade_assessment_id: Mapped[int] = mapped_column(ForeignKey("post_trade_assessments.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_evidence_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_policy_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    setup_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    regime_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    criterion_grades: Mapped[str] = mapped_column(Text, nullable=False)
    violation_codes: Mapped[str] = mapped_column(Text, nullable=False)
    hard_rule_codes: Mapped[str] = mapped_column(Text, nullable=False)
    declared_actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    post_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_position_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_trade_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    archived_at: Mapped[str] = mapped_column(String(64), nullable=False)


class FrameworkRuleSettings(Base):
    """Trader-wide hard-rule configuration. All flags are advisory in this app."""

    __tablename__ = "framework_rule_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    oversized_revenge_hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mandatory_setup_hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    stop_widened_hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    shutdown_breach_hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    repeated_critical_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)


class FrameworkPeriodReview(Base):
    """A saved weekly or monthly reflection with immutable calculated metrics."""

    __tablename__ = "framework_period_reviews"
    __table_args__ = (UniqueConstraint("mt5_account_id", "cadence", "period_start", "period_end", name="uq_framework_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    cadence: Mapped[str] = mapped_column(String(12), nullable=False)
    period_start: Mapped[str] = mapped_column(String(10), nullable=False)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="reviewed")
    psychology_score: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_score: Mapped[str | None] = mapped_column(String, nullable=True)
    system_score: Mapped[str | None] = mapped_column(String, nullable=True)
    readiness_score: Mapped[str | None] = mapped_column(String, nullable=True)
    alert_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recurring_issues: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    review_note: Mapped[str] = mapped_column(Text, nullable=False)
    priority_action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class PillarRoadmapEvidence(Base):
    __tablename__ = "pillar_roadmap_evidence"
    __table_args__ = (UniqueConstraint("scope_key", "pillar", "level", "item_key", name="uq_pillar_roadmap_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False)
    pillar: Mapped[str] = mapped_column(String(24), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    item_key: Mapped[str] = mapped_column(String(80), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class FrameworkFocus(Base):
    """One deliberate, measurable improvement focus per account."""

    __tablename__ = "framework_focuses"
    __table_args__ = (Index("uq_active_framework_focus", "account_id", unique=True, sqlite_where=text("status = 'active'")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(32), nullable=False, default="trader")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=True)
    pillar: Mapped[str] = mapped_column(String(24), nullable=False)
    metric_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    metric_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_value: Mapped[str | None] = mapped_column(String, nullable=True)
    target_value: Mapped[str] = mapped_column(String, nullable=False)
    target_reviews: Mapped[int] = mapped_column(Integer, nullable=False)
    starting_manual_reviews: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    coach_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


@dataclass(frozen=True)
class MT5AccountView:
    id: int
    account_currency: str


@dataclass(frozen=True)
class AccountListItem:
    id: int
    display_name: str
    login: str
    broker_server: str
    account_currency: str
    export_file_path: str
    opening_balance: str | None
    latest_mt5_balance: str | None
    latest_server_utc_offset_minutes: int | None
    strategy_profile_id: int
    strategy_name: str

    @property
    def funded_capital(self) -> str | None:
        """Fixed capital reference retained under the legacy database field name."""
        return self.opening_balance


@dataclass(frozen=True)
class TradeListItem:
    source: str
    position_id: str | None
    symbol: str
    direction: str
    exit_time: str
    net_pnl: str
    result_r: str | None
    strategy: str | None
    strategy_source: str
    effective_risk: str | None
    risk_source: str


@dataclass(frozen=True)
class JournalSettingsView:
    reporting_time_basis: str
    display_language: str
    default_strategy_name: str | None
    default_strategy_profile_id: int | None


@dataclass(frozen=True)
class StrategyProfileView:
    id: int
    name: str
    description: str | None
    backtest_verified: bool
    backtest_notes: str | None
    magic_numbers: tuple[str, ...]


@dataclass(frozen=True)
class StrategySetupView:
    id: int
    strategy_profile_id: int
    name: str
    description: str | None
    active: bool


@dataclass(frozen=True)
class ReviewContextTagView:
    id: int
    kind: str
    name: str
    active: bool


@dataclass(frozen=True)
class ReviewContextSelection:
    strategy_setup_id: int | None = None
    session_tag_id: int | None = None
    regime_tag_id: int | None = None


@dataclass(frozen=True)
class TradePerformanceItem:
    logical_trade_id: int
    display_label: str
    position_ids: tuple[str, ...]
    position_count: int
    exit_time: str
    server_utc_offset_minutes: int
    position_id: str | None
    symbol: str
    direction: str
    net_pnl: str
    result_r: str | None
    strategy: str | None


@dataclass(frozen=True)
class AccountBalanceMovement:
    """One immutable realized MT5 cash movement for balance reporting."""

    position_id: str | None
    exit_time: str
    server_utc_offset_minutes: int
    net_pnl: str
    result_r: str | None


@dataclass(frozen=True)
class AccountRiskPolicyView:
    id: int
    account_id: int
    version: int
    standard_risk_per_trade_percent: str
    maximum_risk_per_trade_percent: str
    daily_loss_limit_r: str
    weekly_loss_limit_r: str
    max_drawdown_percent: str
    drawdown_reset_period: str
    max_open_risk_r: str
    max_consecutive_losses: int
    loss_streak_reset_period: str
    minimum_rr: str
    correlation_policy: str | None
    pretrade_balance_auto_evidence_enabled: bool
    server_utc_offset_minutes: int | None
    created_at: str


@dataclass(frozen=True)
class LivePositionItem:
    position_id: str
    snapshot_time: str
    source_updated_at: str
    symbol: str
    direction: str
    entry_time: str
    entry_price: str
    current_price: str
    volume: str
    stop_price: str | None
    target_price: str | None
    net_unrealized_pnl: str
    risk_to_stop_amount: str | None
    magic_number: str | None


@dataclass(frozen=True)
class LivePositionIncidentItem:
    id: int
    category: str
    state: str
    position_id: str | None
    detail: str
    occurred_at: str


@dataclass(frozen=True)
class LiveSnapshotItem:
    snapshot_time: str
    export_interval_seconds: int
    source_updated_at: str
    source_file_mtime_ns: int | None
    source_file_size: int | None


@dataclass(frozen=True)
class StrategyEvidenceSnapshot:
    profile_id: int
    name: str
    description: str | None
    backtest_verified: bool
    backtest_notes: str | None


@dataclass(frozen=True)
class ImportedPositionReviewItem:
    id: int
    account_id: int
    logical_trade_id: int
    position_id: str | None
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    server_utc_offset_minutes: int
    entry_price: str
    exit_price: str
    volume: str
    net_pnl: str
    entry_stop_price: str | None
    entry_target_price: str | None
    close_stop_price: str | None
    entry_magic_number: str | None
    entry_deal_count: int | None
    exit_reason: str | None
    initial_risk_amount: str | None
    initial_reward_amount: str | None
    pretrade_account_balance: str | None
    auto_risk_policy_id: int | None


@dataclass(frozen=True)
class ClosedTradeReviewItem:
    """Aggregate execution facts for one reviewable logical trade."""

    id: int
    position_id: str | None
    position_ids: tuple[str, ...]
    custom_label: str | None
    display_label: str
    members: tuple[ImportedPositionReviewItem, ...]
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    server_utc_offset_minutes: int
    entry_price: str
    exit_price: str
    volume: str
    net_pnl: str
    entry_stop_price: str | None
    entry_target_price: str | None
    close_stop_price: str | None
    entry_magic_number: str | None
    entry_deal_count: int | None
    exit_reason: str | None
    initial_risk_amount: str | None
    initial_reward_amount: str | None
    auto_risk_policy_id: int | None

    @property
    def position_count(self) -> int:
        return len(self.members)

    @property
    def is_group(self) -> bool:
        return self.position_count > 1


@dataclass(frozen=True)
class PostTradeAssessmentView:
    id: int
    account_id: int
    trade_id: int
    method: str
    risk_policy_id: int | None
    risk_evidence_source: str | None
    risk_policy_state: str | None
    strategy_profile_id: int | None
    strategy_snapshot: "StrategyEvidenceSnapshot | None"
    setup_snapshot: str | None
    session_snapshot: str | None
    regime_snapshot: str | None
    criterion_grades: dict[str, str]
    violation_codes: tuple[str, ...]
    hard_rule_codes: tuple[str, ...]
    declared_actual_risk_amount: str | None
    post_review_note: str | None
    corrective_action: str | None
    assessed_position_ids: tuple[str, ...]
    assessed_trade_label: str
    superseded_at: str | None
    superseded_reason: str | None
    created_at: str
    updated_at: str
    version: int


@dataclass(frozen=True)
class PostTradeAssessmentRevisionView:
    version: int
    method: str
    risk_policy_id: int | None
    risk_evidence_source: str | None
    risk_policy_state: str | None
    strategy_profile_id: int | None
    strategy_snapshot: "StrategyEvidenceSnapshot | None"
    setup_snapshot: str | None
    session_snapshot: str | None
    regime_snapshot: str | None
    criterion_grades: dict[str, str]
    violation_codes: tuple[str, ...]
    hard_rule_codes: tuple[str, ...]
    declared_actual_risk_amount: str | None
    post_review_note: str | None
    corrective_action: str | None
    assessed_position_ids: tuple[str, ...]
    assessed_trade_label: str | None
    archived_at: str


@dataclass(frozen=True)
class PostTradeAssessmentOutcome:
    assessment: PostTradeAssessmentView
    trade: ClosedTradeReviewItem


@dataclass(frozen=True)
class LogicalTradeRegroupPreview:
    affected_assessment_count: int
    affected_assessment_labels: tuple[str, ...]


@dataclass(frozen=True)
class LogicalTradeRegroupResult:
    logical_trade_id: int | None
    superseded_assessment_count: int


@dataclass(frozen=True)
class FrameworkRuleSettingsView:
    oversized_revenge_hard: bool
    mandatory_setup_hard: bool
    stop_widened_hard: bool
    shutdown_breach_hard: bool
    repeated_critical_threshold: int


@dataclass(frozen=True)
class FrameworkPeriodReviewView:
    id: int
    account_id: int
    cadence: str
    period_start: str
    period_end: str
    status: str
    psychology_score: str | None
    risk_score: str | None
    system_score: str | None
    readiness_score: str | None
    alert_codes: tuple[str, ...]
    recurring_issues: tuple[str, ...]
    review_note: str
    priority_action: str
    created_at: str


@dataclass(frozen=True)
class PillarRoadmapEvidenceView:
    scope_key: str
    pillar: str
    level: int
    item_key: str
    completed: bool
    evidence_note: str | None
    updated_at: str


@dataclass(frozen=True)
class FrameworkFocusView:
    id: int
    account_id: int | None
    pillar: str
    metric_kind: str
    metric_code: str | None
    hypothesis: str
    action_text: str
    baseline_value: str | None
    target_value: str
    target_reviews: int
    starting_manual_reviews: int
    status: str
    source: str
    coach_reason: str | None
    resolution_note: str | None
    created_at: str
    resolved_at: str | None


class SQLiteJournalRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        # The desktop sync worker and the Streamlit UI are separate local
        # processes. WAL keeps reads responsive; the timeout lets short form
        # saves wait for an import transaction instead of failing immediately.
        self._engine = create_engine(f"sqlite:///{self._database_path}", connect_args={"timeout": 10})

        @event.listens_for(self._engine, "connect")
        def configure_sqlite(connection, _record) -> None:  # type: ignore[no-untyped-def]
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")

        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    @property
    def database_path(self) -> Path:
        return self._database_path

    def close(self) -> None:
        """Release pooled SQLite connections held by this repository."""

        self._engine.dispose()

    def initialize(self) -> None:
        self._require_clean_framework_schema()
        Base.metadata.create_all(self._engine)
        with self._engine.begin() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(journal_settings)")}
            if "display_language" not in columns:
                connection.exec_driver_sql("ALTER TABLE journal_settings ADD COLUMN display_language VARCHAR(2) NOT NULL DEFAULT 'en'")
            if "active_mt5_account_id" not in columns:
                connection.exec_driver_sql("ALTER TABLE journal_settings ADD COLUMN active_mt5_account_id INTEGER REFERENCES mt5_accounts(id)")
            import_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(mt5_import_runs)")}
            if "source_file_mtime_ns" not in import_columns:
                connection.exec_driver_sql("ALTER TABLE mt5_import_runs ADD COLUMN source_file_mtime_ns INTEGER")
            if "source_file_size" not in import_columns:
                connection.exec_driver_sql("ALTER TABLE mt5_import_runs ADD COLUMN source_file_size INTEGER")
            live_snapshot_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(live_position_snapshots)")}
            if live_snapshot_columns and "source_file_mtime_ns" not in live_snapshot_columns:
                connection.exec_driver_sql("ALTER TABLE live_position_snapshots ADD COLUMN source_file_mtime_ns INTEGER")
            if live_snapshot_columns and "source_file_size" not in live_snapshot_columns:
                connection.exec_driver_sql("ALTER TABLE live_position_snapshots ADD COLUMN source_file_size INTEGER")
            if live_snapshot_columns and "export_interval_seconds" not in live_snapshot_columns:
                connection.exec_driver_sql("ALTER TABLE live_position_snapshots ADD COLUMN export_interval_seconds INTEGER NOT NULL DEFAULT 60")
            assessment_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(post_trade_assessments)")}
            for column_name, column_type in (
                ("setup_snapshot", "VARCHAR(100)"),
                ("session_snapshot", "VARCHAR(80)"),
                ("regime_snapshot", "VARCHAR(80)"),
            ):
                if column_name not in assessment_columns:
                    connection.exec_driver_sql(f"ALTER TABLE post_trade_assessments ADD COLUMN {column_name} {column_type}")
            revision_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(post_trade_assessment_revisions)")}
            for column_name, column_type in (
                ("setup_snapshot", "VARCHAR(100)"),
                ("session_snapshot", "VARCHAR(80)"),
                ("regime_snapshot", "VARCHAR(80)"),
                ("assessed_position_ids", "TEXT"),
                ("assessed_trade_label", "VARCHAR(160)"),
            ):
                if column_name not in revision_columns:
                    connection.exec_driver_sql(f"ALTER TABLE post_trade_assessment_revisions ADD COLUMN {column_name} {column_type}")
            focus_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(framework_focuses)")}
            for column_name, column_type in (("source", "VARCHAR(16) NOT NULL DEFAULT 'manual'"), ("coach_reason", "TEXT")):
                if column_name not in focus_columns:
                    connection.exec_driver_sql(f"ALTER TABLE framework_focuses ADD COLUMN {column_name} {column_type}")
            strategy_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(strategy_profiles)")}
            if "backtest_verified" not in strategy_columns:
                connection.exec_driver_sql("ALTER TABLE strategy_profiles ADD COLUMN backtest_verified BOOLEAN NOT NULL DEFAULT 0")
            period_review_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(framework_period_reviews)")}
            if period_review_columns and "status" not in period_review_columns:
                connection.exec_driver_sql("ALTER TABLE framework_period_reviews ADD COLUMN status VARCHAR(12) NOT NULL DEFAULT 'reviewed'")
            risk_policy_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(account_risk_policies)")}
            if risk_policy_columns and "drawdown_reset_period" not in risk_policy_columns:
                connection.exec_driver_sql("ALTER TABLE account_risk_policies ADD COLUMN drawdown_reset_period VARCHAR(16) NOT NULL DEFAULT 'daily'")
            if risk_policy_columns and "loss_streak_reset_period" not in risk_policy_columns:
                connection.exec_driver_sql("ALTER TABLE account_risk_policies ADD COLUMN loss_streak_reset_period VARCHAR(16) NOT NULL DEFAULT 'daily'")
            if risk_policy_columns and "server_utc_offset_minutes" not in risk_policy_columns:
                connection.exec_driver_sql("ALTER TABLE account_risk_policies ADD COLUMN server_utc_offset_minutes INTEGER")
            if risk_policy_columns:
                connection.exec_driver_sql(
                    "UPDATE account_risk_policies "
                    "SET server_utc_offset_minutes = ("
                    "SELECT latest_server_utc_offset_minutes FROM mt5_accounts "
                    "WHERE mt5_accounts.id = account_risk_policies.mt5_account_id"
                    ") WHERE server_utc_offset_minutes IS NULL"
                )
            # create_all does not add newly-declared indexes to an existing table.
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_trades_account_exit ON trades (mt5_account_id, exit_time, id)",
                "CREATE INDEX IF NOT EXISTS ix_trades_logical_trade ON trades (logical_trade_id)",
                "CREATE INDEX IF NOT EXISTS ix_import_runs_account_path_status_id ON mt5_import_runs (mt5_account_id, source_file_path, status, id)",
                "CREATE INDEX IF NOT EXISTS ix_active_assessments_account ON post_trade_assessments (mt5_account_id) WHERE superseded_at IS NULL",
            ):
                connection.exec_driver_sql(statement)
            # uq_active_framework_focus used to target scope_key (one active focus in the
            # whole app); it now targets account_id (one active focus per account) - an
            # existing index of that name blocks create_all from redefining it, so migrate it.
            connection.exec_driver_sql("DROP INDEX IF EXISTS uq_active_framework_focus")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_framework_focus ON framework_focuses (account_id) WHERE status = 'active'")
        with self._sessions.begin() as session:
            settings = session.get(JournalSettings, 1)
            if settings is None:
                settings = JournalSettings(id=1, reporting_time_basis="server", display_language="en")
                session.add(settings)
                session.flush()
            if settings.default_strategy_profile_id is None and session.scalar(select(StrategyProfile.id).limit(1)) is None:
                default = session.scalar(select(StrategyProfile).where(StrategyProfile.normalized_name == "journal default"))
                if default is None:
                    default = StrategyProfile(
                        name="Journal default",
                        normalized_name="journal default",
                        description=None,
                    )
                    session.add(default)
                    session.flush()
                settings.default_strategy_profile_id = default.id
                settings.default_strategy_name = default.name
            # One-time reconciliation for the removed "Psychology is trader-wide" scoping:
            # legacy rows persisted with account_id=None / scope_key="trader" for psychology
            # must be resolved to the active account, or the now account-scoped code paths
            # would either crash on a None account_id or silently orphan prior evidence.
            active_account_id = settings.active_mt5_account_id
            if active_account_id is not None:
                stale_focus_rows = session.scalars(
                    select(FrameworkFocus).where(
                        FrameworkFocus.pillar == "psychology", FrameworkFocus.account_id.is_(None), FrameworkFocus.status == "active"
                    )
                ).all()
                for row in stale_focus_rows:
                    row.status = "abandoned"
                    row.resolution_note = "Superseded: Psychology coaching focuses are now scoped to one account instead of combined across all accounts."
                    row.resolved_at = datetime.now(timezone.utc).isoformat()
                stale_roadmap_rows = session.scalars(
                    select(PillarRoadmapEvidence).where(
                        PillarRoadmapEvidence.pillar == "psychology", PillarRoadmapEvidence.scope_key == "trader"
                    )
                ).all()
                for row in stale_roadmap_rows:
                    row.scope_key = f"account:{active_account_id}"

    def _require_clean_framework_schema(self) -> None:
        """Greenfield-only persistence: an old database must be reset, never migrated."""
        if not self._database_path.exists():
            return
        expected_columns = {
            "journal_settings": {"reporting_time_basis"},
            "mt5_accounts": {"latest_server_utc_offset_minutes", "strategy_profile_id"},
            "trades": {"server_utc_offset_minutes", "logical_trade_id", "pretrade_account_balance"},
            "account_risk_policies": {"pretrade_balance_auto_evidence_enabled"},
            "logical_trades": {"mt5_account_id", "created_at"},
            "post_trade_assessments": {
                "logical_trade_id",
                "method",
                "criterion_grades",
                "violation_codes",
                "hard_rule_codes",
                "assessed_position_ids",
                "assessed_trade_label",
                "superseded_at",
                "superseded_reason",
            },
            "post_trade_assessment_revisions": {"method", "criterion_grades", "violation_codes", "hard_rule_codes"},
            "live_positions": {"mt5_account_id", "mt5_position_id"},
            "live_position_snapshots": {"mt5_account_id", "snapshot_time"},
            "live_position_incidents": {"mt5_account_id"},
            "strategy_setups": {"strategy_profile_id", "name"},
            "review_context_tags": {"kind", "name"},
            "framework_focuses": {"account_id", "status"},
            "pillar_roadmap_evidence": {"scope_key", "pillar", "level", "item_key"},
            "framework_period_reviews": {"mt5_account_id", "cadence", "period_start", "period_end"},
        }
        with self._engine.connect() as connection:
            tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            existing_journal_tables = {"journal_settings", "mt5_accounts", "trades", "account_risk_policies", "post_trade_assessments"}
            if tables.intersection(existing_journal_tables) and "framework_rule_settings" not in tables:
                raise JournalDatabaseResetRequiredError(
                    "This database predates the greenfield three-pillar framework. "
                    "Reset it before starting the app: make reset-db CONFIRM_RESET=yes"
                )
            for table_name, required in expected_columns.items():
                if table_name not in tables:
                    continue
                columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})")}
                if not required.issubset(columns):
                    raise JournalDatabaseResetRequiredError(
                        "This database predates the greenfield three-pillar framework. "
                        "Reset it before starting the app: make reset-db CONFIRM_RESET=yes"
                    )
            if "mt5_accounts" in tables:
                unique_indexes = connection.exec_driver_sql("PRAGMA index_list(mt5_accounts)").fetchall()
                has_unique_login = any(
                    index[2]
                    and [column[2] for column in connection.exec_driver_sql(f"PRAGMA index_info({index[1]})")] == ["login"]
                    for index in unique_indexes
                )
                if not has_unique_login:
                    raise JournalDatabaseResetRequiredError(
                        "This database uses the previous MT5 account identity rule. "
                        "Reset it before starting the app: make reset-db CONFIRM_RESET=yes"
                    )

    def configure_journal(
        self,
        *,
        reporting_time_basis: str,
        display_language: str | None = None,
    ) -> None:
        if reporting_time_basis not in REPORTING_TIME_BASES:
            raise ValueError("Reporting time must be UTC, Server Timezone, or Local Timezone")
        if display_language is not None and display_language not in {"en", "vi"}:
            raise ValueError("Display language must be English or Vietnamese")
        with self._sessions.begin() as session:
            settings = session.get(JournalSettings, 1)
            if settings is None:
                session.add(
                    JournalSettings(
                        id=1,
                        reporting_time_basis=reporting_time_basis,
                        display_language=display_language or "en",
                        default_strategy_name=None,
                        default_strategy_profile_id=None,
                    )
                )
            else:
                settings.reporting_time_basis = reporting_time_basis
                if display_language is not None:
                    settings.display_language = display_language

    def get_journal_settings(self) -> JournalSettingsView:
        with self._sessions() as session:
            settings = session.get(JournalSettings, 1)
            if settings is None:
                raise RuntimeError("Journal settings have not been configured")
            default_strategy_name = settings.default_strategy_name
            if settings.default_strategy_profile_id is not None:
                profile = session.get(StrategyProfile, settings.default_strategy_profile_id)
                if profile is not None:
                    default_strategy_name = profile.name
            return JournalSettingsView(
                settings.reporting_time_basis,
                settings.display_language,
                default_strategy_name,
                settings.default_strategy_profile_id,
            )

    def register_mt5_account(
        self,
        *,
        display_name: str,
        login: str,
        broker_server: str,
        account_currency: str,
        export_file_path: str,
        opening_balance: str | None = None,
        strategy_profile_id: int | None = None,
    ) -> None:
        """Test/bootstrap-only account upsert — the app itself always uses `create_configured_mt5_account`.

        Deliberately looser than the production path: it silently updates an
        existing login instead of rejecting the duplicate, and it does not
        check `active`/disabled-account state. Do not call this from `app.py`
        or `presentation/` — those invariants belong to the production flow.
        """
        baseline = None if opening_balance is None or not opening_balance.strip() else _decimal_string(
            self._required_decimal(opening_balance, "Funded capital", minimum=Decimal("0.01"))
        )
        with self._sessions.begin() as session:
            if strategy_profile_id is None:
                settings = session.get(JournalSettings, 1)
                strategy_profile_id = (
                    settings.default_strategy_profile_id if settings is not None else None
                ) or session.scalar(select(StrategyProfile.id).order_by(StrategyProfile.id).limit(1))
            strategy = None if strategy_profile_id is None else session.get(StrategyProfile, strategy_profile_id)
            if strategy is None:
                raise ValueError("Create a strategy before adding an MT5 account")
            existing = session.scalar(select(MT5Account).where(MT5Account.login == login))
            if existing is None:
                session.add(
                    MT5Account(
                        display_name=display_name,
                        login=login,
                        broker_server=broker_server,
                        account_currency=account_currency.upper(),
                        export_file_path=export_file_path,
                        opening_balance=baseline,
                        strategy_profile_id=strategy.id,
                        active=True,
                    )
                )
            else:
                if existing.broker_server != broker_server:
                    raise ValueError("This MT5 account ID is already registered with a different broker server")
                existing.display_name = display_name
                existing.account_currency = account_currency.upper()
                existing.export_file_path = export_file_path
                if existing.strategy_profile_id != strategy.id:
                    raise ValueError("An MT5 account stays with its original strategy")
                if baseline is not None:
                    existing.opening_balance = baseline
                existing.active = True

    def create_configured_mt5_account(
        self,
        *,
        display_name: str,
        login: str,
        broker_server: str,
        account_currency: str,
        export_file_path: str,
        funded_capital: str,
        strategy_profile_id: int | None,
        strategy_name: str | None,
        strategy_description: str | None,
        standard_risk_per_trade_percent: str,
        maximum_risk_per_trade_percent: str,
        daily_loss_limit_r: str,
        weekly_loss_limit_r: str,
        max_drawdown_percent: str,
        max_open_risk_r: str,
        max_consecutive_losses: int,
        minimum_rr: str,
        correlation_policy: str | None,
        drawdown_reset_period: str = "daily",
        loss_streak_reset_period: str = "daily",
    ) -> AccountListItem:
        """Atomically create an import-ready account, system baseline, and risk policy."""
        clean_display_name = self._required_text(display_name, "Account name")
        clean_login = self._required_text(login, "MT5 account ID")
        if not clean_login.isdecimal():
            raise ValueError("MT5 account ID must be numeric")
        clean_broker = self._required_text(broker_server, "Broker server")
        clean_currency = self._required_text(account_currency, "Currency").upper()
        if len(clean_currency) != 3 or not clean_currency.isalpha():
            raise ValueError("Currency must be a three-letter code")
        capital = _decimal_string(self._required_decimal(funded_capital, "Funded capital", minimum=Decimal("0.01")))
        risk_inputs = self._validated_risk_policy_inputs(
            standard_risk_per_trade_percent=standard_risk_per_trade_percent,
            maximum_risk_per_trade_percent=maximum_risk_per_trade_percent,
            daily_loss_limit_r=daily_loss_limit_r,
            weekly_loss_limit_r=weekly_loss_limit_r,
            max_drawdown_percent=max_drawdown_percent,
            max_open_risk_r=max_open_risk_r,
            max_consecutive_losses=max_consecutive_losses,
            minimum_rr=minimum_rr,
            drawdown_reset_period=drawdown_reset_period,
            loss_streak_reset_period=loss_streak_reset_period,
        )
        creating_strategy = strategy_profile_id is None
        if creating_strategy:
            clean_strategy_name = self._required_text(strategy_name or "", "Strategy name")
            clean_strategy_description = self._required_text(strategy_description or "", "Strategy description")
            normalized_strategy_name = normalize_strategy_name(clean_strategy_name)
        with self._sessions.begin() as session:
            duplicate = session.scalar(select(MT5Account).where(MT5Account.login == clean_login))
            if duplicate is not None:
                if not duplicate.active:
                    raise ValueError("This MT5 account ID is disabled. Reactivate it from Disabled accounts")
                raise ValueError("This MT5 account ID is already registered")
            if creating_strategy:
                if session.scalar(select(StrategyProfile.id).where(StrategyProfile.normalized_name == normalized_strategy_name)) is not None:
                    raise ValueError("A strategy with this name already exists")
                strategy = StrategyProfile(
                    name=clean_strategy_name,
                    normalized_name=normalized_strategy_name,
                    description=clean_strategy_description,
                )
                session.add(strategy)
                session.flush()
            else:
                strategy = session.get(StrategyProfile, strategy_profile_id)
                if strategy is None or strategy.name == "Journal default":
                    raise ValueError("Select a saved trading system or create one in this flow")
                if not self._optional_text(strategy.description):
                    raise ValueError("The selected trading system needs a strategy description")
            account = MT5Account(
                display_name=clean_display_name,
                login=clean_login,
                broker_server=clean_broker,
                account_currency=clean_currency,
                export_file_path=export_file_path,
                opening_balance=capital,
                strategy_profile_id=strategy.id,
                active=True,
            )
            session.add(account)
            session.flush()
            session.add(AccountRiskPolicy(
                mt5_account_id=account.id,
                version=1,
                active=True,
                risk_per_trade_percent=risk_inputs["standard"],
                maximum_risk_per_trade_percent=risk_inputs["maximum"],
                daily_loss_limit_r=risk_inputs["daily"],
                weekly_loss_limit_r=risk_inputs["weekly"],
                max_drawdown_percent=risk_inputs["drawdown"],
                drawdown_reset_period=risk_inputs["drawdown_reset_period"],
                max_open_risk_r=risk_inputs["open_risk"],
                max_consecutive_losses=max_consecutive_losses,
                loss_streak_reset_period=risk_inputs["loss_streak_reset_period"],
                minimum_rr=risk_inputs["minimum_rr"],
                correlation_policy=self._optional_text(correlation_policy),
                pretrade_balance_auto_evidence_enabled=False,
                server_utc_offset_minutes=account.latest_server_utc_offset_minutes,
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
            settings = session.get(JournalSettings, 1)
            if settings is not None:
                settings.active_mt5_account_id = account.id
            session.flush()
            return self._to_account_list_item(account)

    def account_has_imported_trades(self, account_id: int) -> bool:
        with self._sessions() as session:
            return session.scalar(select(Trade.id).where(Trade.mt5_account_id == account_id).limit(1)) is not None

    def update_mt5_account(
        self,
        *,
        account_id: int,
        display_name: str,
        login: str,
        broker_server: str,
        account_currency: str,
        export_file_path: str,
        opening_balance: str | None,
        strategy_profile_id: int | None = None,
    ) -> None:
        """Update one approved account without rewriting its imported MT5 history."""
        baseline = None if opening_balance is None or not opening_balance.strip() else _decimal_string(
            self._required_decimal(opening_balance, "Funded capital", minimum=Decimal("0.01"))
        )
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("The selected MT5 account no longer exists")

            identity_changed = (
                account.login != login
                or account.broker_server != broker_server
                or account.account_currency != account_currency.upper()
            )
            has_imported_trades = session.scalar(select(Trade.id).where(Trade.mt5_account_id == account_id).limit(1)) is not None
            if identity_changed and has_imported_trades:
                raise ValueError("MT5 account ID, broker server, and currency cannot change after trades are imported")

            duplicate = session.scalar(
                select(MT5Account).where(
                    MT5Account.login == login,
                    MT5Account.id != account_id,
                )
            )
            if duplicate is not None:
                raise ValueError("Another account already uses this MT5 account ID")

            if strategy_profile_id is not None and strategy_profile_id != account.strategy_profile_id:
                if has_imported_trades:
                    raise ValueError("An account's trading system is locked once trades are imported")
                strategy = session.get(StrategyProfile, strategy_profile_id)
                if strategy is None:
                    raise ValueError("Select a saved trading system")
                account.strategy_profile_id = strategy.id

            account.display_name = display_name
            account.login = login
            account.broker_server = broker_server
            account.account_currency = account_currency.upper()
            account.export_file_path = export_file_path
            account.opening_balance = baseline
            account.active = True

    def deactivate_mt5_account(self, account_id: int) -> None:
        """Hide an obsolete account from imports and reports while retaining its history."""
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("The selected MT5 account no longer exists")
            account.active = False
            settings = session.get(JournalSettings, 1)
            if settings is not None and settings.active_mt5_account_id == account_id:
                settings.active_mt5_account_id = None

    def reactivate_mt5_account(self, account_id: int) -> None:
        """Restore a disabled account without rewriting history or changing account selection."""
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("The selected MT5 account no longer exists")
            if account.active:
                return
            account.active = True

    def delete_mt5_account(self, account_id: int) -> None:
        """Permanently delete an unimported account and its account-only setup."""
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("The selected MT5 account no longer exists")
            if session.scalar(select(Trade.id).where(Trade.mt5_account_id == account_id).limit(1)) is not None:
                raise ValueError("An account with imported trades cannot be deleted. Disable it to retain its history instead")
            session.execute(delete(MT5ImportRun).where(MT5ImportRun.mt5_account_id == account_id))
            session.execute(delete(LivePositionIncident).where(LivePositionIncident.mt5_account_id == account_id))
            session.execute(delete(LivePosition).where(LivePosition.mt5_account_id == account_id))
            session.execute(delete(LivePositionSnapshot).where(LivePositionSnapshot.mt5_account_id == account_id))
            session.execute(delete(AccountRiskPolicy).where(AccountRiskPolicy.mt5_account_id == account_id))
            session.execute(delete(FrameworkFocus).where(FrameworkFocus.account_id == account_id))
            session.execute(delete(FrameworkPeriodReview).where(FrameworkPeriodReview.mt5_account_id == account_id))
            # SQLite reuses this integer id for the next account created, so a deleted
            # account's roadmap progress must not be left behind to be inherited by it.
            session.execute(delete(PillarRoadmapEvidence).where(PillarRoadmapEvidence.scope_key == self._roadmap_scope_key(account_id)))
            settings = session.get(JournalSettings, 1)
            if settings is not None and settings.active_mt5_account_id == account_id:
                settings.active_mt5_account_id = None
                session.flush()
            session.delete(account)

    def find_active_mt5_account(self, login: str, broker_server: str) -> MT5AccountView | None:
        with self._sessions() as session:
            account = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server, MT5Account.active.is_(True)))
            return None if account is None else MT5AccountView(id=account.id, account_currency=account.account_currency)

    def get_account_strategy(self, account_id: int) -> StrategyProfileView:
        with self._sessions() as session:
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("The selected MT5 account no longer exists")
            return self._to_strategy_profile_view(account.strategy_profile)

    @staticmethod
    def _to_account_list_item(account: MT5Account) -> AccountListItem:
        return AccountListItem(
            account.id,
            account.display_name,
            account.login,
            account.broker_server,
            account.account_currency,
            account.export_file_path,
            account.opening_balance,
            account.latest_mt5_balance,
            account.latest_server_utc_offset_minutes,
            account.strategy_profile_id,
            account.strategy_profile.name,
        )

    def list_mt5_accounts(self) -> list[AccountListItem]:
        with self._sessions() as session:
            accounts = session.scalars(select(MT5Account).where(MT5Account.active.is_(True)).order_by(MT5Account.display_name)).all()
            return [self._to_account_list_item(account) for account in accounts]

    def list_disabled_mt5_accounts(self) -> list[AccountListItem]:
        """List retained accounts that are currently excluded from imports and reports."""
        with self._sessions() as session:
            accounts = session.scalars(select(MT5Account).where(MT5Account.active.is_(False)).order_by(MT5Account.display_name)).all()
            return [self._to_account_list_item(account) for account in accounts]

    def get_active_mt5_account(self) -> AccountListItem | None:
        """Return the single account used app-wide, self-healing to a deterministic fallback if unset or stale."""
        with self._sessions.begin() as session:
            accounts = session.scalars(select(MT5Account).where(MT5Account.active.is_(True)).order_by(MT5Account.display_name)).all()
            if not accounts:
                return None
            settings = session.get(JournalSettings, 1)
            active_id = settings.active_mt5_account_id if settings is not None else None
            account = next((item for item in accounts if item.id == active_id), None)
            if account is None:
                account = accounts[0]
                if settings is not None:
                    settings.active_mt5_account_id = account.id
            return self._to_account_list_item(account)

    def set_active_mt5_account(self, account_id: int) -> None:
        """Switch the account used app-wide across Dashboard and Framework pages."""
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None or not account.active:
                raise ValueError("The selected MT5 account is not available")
            settings = session.get(JournalSettings, 1)
            if settings is None:
                raise RuntimeError("Journal settings have not been configured")
            settings.active_mt5_account_id = account_id

    def get_account_opening_balance(self, account_id: int) -> str | None:
        with self._sessions() as session:
            account = session.get(MT5Account, account_id)
            return None if account is None else account.opening_balance

    def get_account_funded_capital(self, account_id: int) -> str | None:
        """Return the fixed funded-capital reference for risk limits and drawdown."""
        return self.get_account_opening_balance(account_id)

    def get_latest_mt5_balance(self, account_id: int) -> str | None:
        with self._sessions() as session:
            account = session.get(MT5Account, account_id)
            return None if account is None else account.latest_mt5_balance

    def get_active_risk_policy(self, account_id: int) -> AccountRiskPolicyView | None:
        with self._sessions() as session:
            policy = session.scalar(
                select(AccountRiskPolicy)
                .where(AccountRiskPolicy.mt5_account_id == account_id, AccountRiskPolicy.active.is_(True))
                .order_by(AccountRiskPolicy.version.desc())
            )
            return None if policy is None else self._to_risk_policy_view(policy)

    def get_risk_policy(self, policy_id: int) -> AccountRiskPolicyView | None:
        with self._sessions() as session:
            policy = session.get(AccountRiskPolicy, policy_id)
            return None if policy is None else self._to_risk_policy_view(policy)

    def list_account_risk_policies(self, account_id: int) -> list[AccountRiskPolicyView]:
        """Return the complete version history used to detect monitoring epochs."""
        with self._sessions() as session:
            policies = session.scalars(
                select(AccountRiskPolicy)
                .where(AccountRiskPolicy.mt5_account_id == account_id)
                .order_by(AccountRiskPolicy.version)
            ).all()
            return [self._to_risk_policy_view(policy) for policy in policies]

    def replace_live_positions(
        self,
        *,
        login: str,
        broker_server: str,
        account_currency: str,
        snapshot_time: str,
        export_interval_seconds: int,
        positions: list[MT5LivePositionExport],
        source_file_mtime_ns: int | None = None,
        source_file_size: int | None = None,
    ) -> int:
        """Atomically replace one account's disposable live MT5 snapshot."""
        with self._sessions.begin() as session:
            account = session.scalar(
                select(MT5Account).where(
                    MT5Account.login == login,
                    MT5Account.broker_server == broker_server,
                    MT5Account.active.is_(True),
                )
            )
            if account is None:
                raise ValueError("MT5 account is not registered or active")
            if account.account_currency != account_currency:
                raise ValueError("MT5 export currency does not match the registered account")
            snapshot = session.get(LivePositionSnapshot, account.id)
            incoming_time = _parse_live_snapshot_time(snapshot_time)
            if snapshot is not None and incoming_time < _parse_live_snapshot_time(snapshot.snapshot_time):
                return account.id
            session.execute(delete(LivePosition).where(LivePosition.mt5_account_id == account.id))
            now = datetime.now(timezone.utc).isoformat()
            if snapshot is None:
                session.add(LivePositionSnapshot(
                    mt5_account_id=account.id,
                    snapshot_time=snapshot_time,
                    export_interval_seconds=export_interval_seconds,
                    source_updated_at=now,
                    source_file_mtime_ns=source_file_mtime_ns,
                    source_file_size=source_file_size,
                ))
            else:
                snapshot.snapshot_time = snapshot_time
                snapshot.export_interval_seconds = export_interval_seconds
                snapshot.source_updated_at = now
                snapshot.source_file_mtime_ns = source_file_mtime_ns
                snapshot.source_file_size = source_file_size
            for position in positions:
                session.add(
                    LivePosition(
                        mt5_account_id=account.id,
                        mt5_position_id=position.position_id,
                        snapshot_time=snapshot_time,
                        source_updated_at=now,
                        symbol=position.symbol,
                        direction=position.direction,
                        entry_time=position.entry_time,
                        entry_price=_decimal_string(position.entry_price),
                        current_price=_decimal_string(position.current_price),
                        volume=_decimal_string(position.volume),
                        stop_price=None if position.stop_price is None else _decimal_string(position.stop_price),
                        target_price=None if position.target_price is None else _decimal_string(position.target_price),
                        net_unrealized_pnl=_decimal_string(position.net_unrealized_pnl),
                        risk_to_stop_amount=None if position.risk_to_stop_amount is None else _decimal_string(position.risk_to_stop_amount),
                        magic_number=position.magic_number,
                    )
                )
            return account.id

    def get_live_snapshot(self, account_id: int) -> LiveSnapshotItem | None:
        with self._sessions() as session:
            snapshot = session.get(LivePositionSnapshot, account_id)
            return None if snapshot is None else LiveSnapshotItem(
                snapshot.snapshot_time,
                snapshot.export_interval_seconds,
                snapshot.source_updated_at,
                snapshot.source_file_mtime_ns,
                snapshot.source_file_size,
            )

    def list_live_positions(self, account_id: int) -> list[LivePositionItem]:
        with self._sessions() as session:
            rows = session.scalars(
                select(LivePosition)
                .where(LivePosition.mt5_account_id == account_id)
                .order_by(LivePosition.symbol, LivePosition.mt5_position_id)
            ).all()
            return [
                LivePositionItem(
                    position_id=row.mt5_position_id,
                    snapshot_time=row.snapshot_time,
                    source_updated_at=row.source_updated_at,
                    symbol=row.symbol,
                    direction=row.direction,
                    entry_time=row.entry_time,
                    entry_price=row.entry_price,
                    current_price=row.current_price,
                    volume=row.volume,
                    stop_price=row.stop_price,
                    target_price=row.target_price,
                    net_unrealized_pnl=row.net_unrealized_pnl,
                    risk_to_stop_amount=row.risk_to_stop_amount,
                    magic_number=row.magic_number,
                )
                for row in rows
            ]

    def record_live_incident_transitions(
        self, account_id: int, active: dict[str, tuple[str, str | None, str]], *, occurred_at: str
    ) -> None:
        """Append only genuine open/resolve transitions for current live alerts."""
        with self._sessions.begin() as session:
            previous_rows = session.execute(
                select(LivePositionIncident)
                .where(LivePositionIncident.mt5_account_id == account_id)
                .order_by(LivePositionIncident.incident_key, LivePositionIncident.id.desc())
            ).scalars().all()
            latest: dict[str, LivePositionIncident] = {}
            for row in previous_rows:
                latest.setdefault(row.incident_key, row)
            open_keys = {key for key, row in latest.items() if row.state == "opened"}
            for key, (category, position_id, detail) in active.items():
                prior = latest.get(key)
                if prior is None or prior.state == "resolved":
                    session.add(LivePositionIncident(
                        mt5_account_id=account_id,
                        incident_key=key,
                        category=category,
                        state="opened",
                        position_id=position_id,
                        detail=detail,
                        occurred_at=occurred_at,
                    ))
            for key in open_keys - set(active):
                prior = latest[key]
                session.add(LivePositionIncident(
                    mt5_account_id=account_id,
                    incident_key=key,
                    category=prior.category,
                    state="resolved",
                    position_id=prior.position_id,
                    detail=prior.detail,
                    occurred_at=occurred_at,
                ))

    def list_live_position_incidents(self, account_id: int, *, limit: int = 100) -> list[LivePositionIncidentItem]:
        with self._sessions() as session:
            rows = session.scalars(
                select(LivePositionIncident)
                .where(LivePositionIncident.mt5_account_id == account_id)
                .order_by(LivePositionIncident.id.desc())
                .limit(limit)
            ).all()
            return [
                LivePositionIncidentItem(row.id, row.category, row.state, row.position_id, row.detail, row.occurred_at)
                for row in rows
            ]

    def save_account_risk_policy(
        self,
        *,
        account_id: int,
        standard_risk_per_trade_percent: str,
        maximum_risk_per_trade_percent: str,
        daily_loss_limit_r: str,
        weekly_loss_limit_r: str,
        max_drawdown_percent: str,
        max_open_risk_r: str,
        max_consecutive_losses: int,
        minimum_rr: str,
        correlation_policy: str | None,
        pretrade_balance_auto_evidence_enabled: bool = False,
        starting_balance: str | None = None,
        drawdown_reset_period: str = "daily",
        loss_streak_reset_period: str = "daily",
    ) -> AccountRiskPolicyView:
        risk_inputs = self._validated_risk_policy_inputs(
            standard_risk_per_trade_percent=standard_risk_per_trade_percent,
            maximum_risk_per_trade_percent=maximum_risk_per_trade_percent,
            daily_loss_limit_r=daily_loss_limit_r,
            weekly_loss_limit_r=weekly_loss_limit_r,
            max_drawdown_percent=max_drawdown_percent,
            max_open_risk_r=max_open_risk_r,
            max_consecutive_losses=max_consecutive_losses,
            minimum_rr=minimum_rr,
            drawdown_reset_period=drawdown_reset_period,
            loss_streak_reset_period=loss_streak_reset_period,
        )
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None or not account.active:
                raise ValueError("Approved MT5 account was not found")
            if account.opening_balance is None and starting_balance is not None:
                account.opening_balance = _decimal_string(
                    self._required_decimal(starting_balance, "Funded capital", minimum=Decimal("0.01"))
                )
            if account.opening_balance is None:
                raise ValueError("Set funded capital before saving a risk policy")
            active = session.scalar(
                select(AccountRiskPolicy)
                .where(AccountRiskPolicy.mt5_account_id == account_id, AccountRiskPolicy.active.is_(True))
                .order_by(AccountRiskPolicy.version.desc())
            )
            if active is not None:
                active.active = False
            version = 1 if active is None else active.version + 1
            policy = AccountRiskPolicy(
                mt5_account_id=account_id,
                version=version,
                active=True,
                risk_per_trade_percent=risk_inputs["standard"],
                maximum_risk_per_trade_percent=risk_inputs["maximum"],
                daily_loss_limit_r=risk_inputs["daily"],
                weekly_loss_limit_r=risk_inputs["weekly"],
                max_drawdown_percent=risk_inputs["drawdown"],
                drawdown_reset_period=risk_inputs["drawdown_reset_period"],
                max_open_risk_r=risk_inputs["open_risk"],
                max_consecutive_losses=max_consecutive_losses,
                loss_streak_reset_period=risk_inputs["loss_streak_reset_period"],
                minimum_rr=risk_inputs["minimum_rr"],
                correlation_policy=self._optional_text(correlation_policy),
                pretrade_balance_auto_evidence_enabled=pretrade_balance_auto_evidence_enabled,
                server_utc_offset_minutes=account.latest_server_utc_offset_minutes,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(policy)
            session.flush()
            return self._to_risk_policy_view(policy)

    def _validated_risk_policy_inputs(
        self,
        *,
        standard_risk_per_trade_percent: str,
        maximum_risk_per_trade_percent: str,
        daily_loss_limit_r: str,
        weekly_loss_limit_r: str,
        max_drawdown_percent: str,
        max_open_risk_r: str,
        max_consecutive_losses: int,
        minimum_rr: str,
        drawdown_reset_period: str,
        loss_streak_reset_period: str,
    ) -> dict[str, str]:
        standard = self._required_decimal(standard_risk_per_trade_percent, "Standard risk (1R)", minimum=Decimal("0.01"), maximum=Decimal("100"))
        maximum = self._required_decimal(maximum_risk_per_trade_percent, "Maximum risk per trade", minimum=Decimal("0.01"), maximum=Decimal("100"))
        if maximum < standard:
            raise ValueError("Maximum risk per trade must be at least the standard risk (1R)")
        if max_consecutive_losses < 1:
            raise ValueError("Maximum consecutive losses must be at least one")
        if drawdown_reset_period not in MONITORING_RESET_PERIODS:
            raise ValueError("Drawdown reset period must be Daily, Weekly, Monthly, or All time")
        if loss_streak_reset_period not in MONITORING_RESET_PERIODS:
            raise ValueError("Loss-streak reset period must be Daily, Weekly, Monthly, or All time")
        return {
            "standard": _decimal_string(standard),
            "maximum": _decimal_string(maximum),
            "daily": _decimal_string(self._required_decimal(daily_loss_limit_r, "Daily loss limit", minimum=Decimal("0.01"))),
            "weekly": _decimal_string(self._required_decimal(weekly_loss_limit_r, "Weekly loss limit", minimum=Decimal("0.01"))),
            "drawdown": _decimal_string(self._required_decimal(max_drawdown_percent, "Maximum drawdown", minimum=Decimal("0.01"), maximum=Decimal("100"))),
            "drawdown_reset_period": drawdown_reset_period,
            "open_risk": _decimal_string(self._required_decimal(max_open_risk_r, "Maximum open risk", minimum=Decimal("0.01"))),
            "loss_streak_reset_period": loss_streak_reset_period,
            "minimum_rr": _decimal_string(self._required_decimal(minimum_rr, "Minimum R:R", minimum=Decimal("0.01"))),
        }

    def list_closed_trades_for_review(self, account_id: int) -> list[ClosedTradeReviewItem]:
        """Return logical trades assembled from immutable imported MT5 positions."""
        with self._sessions() as session:
            return self._logical_trade_review_items(session, account_id)

    def list_imported_positions_for_risk(self, account_id: int) -> list[ImportedPositionReviewItem]:
        """Return raw imported positions for grouping and audit workflows."""
        with self._sessions() as session:
            rows = session.scalars(
                select(Trade).where(Trade.mt5_account_id == account_id).order_by(Trade.exit_time, Trade.id)
            ).all()
            return [self._to_imported_position_review_item(row) for row in rows]

    def list_imported_positions_for_grouping(self, account_id: int) -> list[ImportedPositionReviewItem]:
        """Return every raw MT5 position that may be moved between logical trades."""
        return self.list_imported_positions_for_risk(account_id)

    def preview_logical_trade_regroup(
        self,
        *,
        account_id: int,
        position_trade_ids: tuple[int, ...],
        logical_trade_id: int | None,
        source_logical_trade_ids: tuple[int, ...] = (),
    ) -> LogicalTradeRegroupPreview:
        """Describe active assessments that a membership change will supersede."""
        selected = tuple(sorted(set(position_trade_ids)))
        with self._sessions() as session:
            selected_rows, destination, destination_rows = self._regroup_context(
                session,
                account_id=account_id,
                position_trade_ids=selected,
                logical_trade_id=logical_trade_id,
            )
            self._validate_complete_logical_trade_sources(
                session,
                account_id=account_id,
                selected_rows=selected_rows,
                source_logical_trade_ids=source_logical_trade_ids,
            )
            if len(selected_rows) > 1:
                self._validate_group_members(selected_rows, account_id)
            affected_ids = self._regroup_affected_logical_trade_ids(
                selected_rows,
                destination,
                destination_rows,
            )
            assessments = self._active_assessments_for_logical_trades(session, affected_ids)
            return LogicalTradeRegroupPreview(
                affected_assessment_count=len(assessments),
                affected_assessment_labels=tuple(item.assessed_trade_label for item in assessments),
            )

    def regroup_logical_trade(
        self,
        *,
        account_id: int,
        position_trade_ids: tuple[int, ...],
        display_label: str | None,
        logical_trade_id: int | None = None,
        source_logical_trade_ids: tuple[int, ...] = (),
        expected_assessment_count: int | None = None,
    ) -> LogicalTradeRegroupResult:
        """Create or change a logical trade without changing immutable MT5 facts.

        Membership is mutable.  Any active assessment attached to a logical
        trade whose member set changes is retained for audit, marked
        superseded, and excluded from active framework evidence.
        """
        selected = tuple(sorted(set(position_trade_ids)))
        if logical_trade_id is None and len(selected) < 2:
            raise ValueError("Select at least two positions to create one logical trade")
        if not selected:
            raise ValueError("Select at least one position")
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            selected_rows, destination, destination_rows = self._regroup_context(
                session,
                account_id=account_id,
                position_trade_ids=selected,
                logical_trade_id=logical_trade_id,
            )
            self._validate_complete_logical_trade_sources(
                session,
                account_id=account_id,
                selected_rows=selected_rows,
                source_logical_trade_ids=source_logical_trade_ids,
            )
            if len(selected_rows) > 1:
                self._validate_group_members(selected_rows, account_id)
            if destination is None:
                destination = LogicalTrade(
                    mt5_account_id=account_id,
                    display_label=self._optional_text(display_label),
                    created_at=now,
                )
                session.add(destination)
                session.flush()
                destination_rows = []
            affected_ids = self._regroup_affected_logical_trade_ids(
                selected_rows,
                destination,
                destination_rows,
            )
            superseded = self._supersede_active_assessments(
                session,
                affected_ids,
                superseded_at=now,
                reason="Logical-trade membership changed",
                expected_count=expected_assessment_count,
            )
            selected_ids = {row.id for row in selected_rows}
            remainder_rows = [row for row in destination_rows if row.id not in selected_ids]
            if remainder_rows:
                remainder = LogicalTrade(
                    mt5_account_id=account_id,
                    display_label=None,
                    created_at=now,
                )
                session.add(remainder)
                session.flush()
                for row in remainder_rows:
                    row.logical_trade_id = remainder.id
            for row in selected_rows:
                row.logical_trade_id = destination.id
            destination.display_label = self._optional_text(display_label)
            self._retire_empty_logical_trades(session, affected_ids - {destination.id})
            return LogicalTradeRegroupResult(destination.id, superseded)

    def preview_logical_trade_disband(
        self,
        *,
        account_id: int,
        logical_trade_id: int,
    ) -> LogicalTradeRegroupPreview:
        with self._sessions() as session:
            group = session.get(LogicalTrade, logical_trade_id)
            if group is None or group.mt5_account_id != account_id:
                raise ValueError("Logical trade was not found for this MT5 account")
            rows = session.scalars(select(Trade).where(Trade.logical_trade_id == logical_trade_id)).all()
            if len(rows) < 2:
                raise ValueError("Only grouped logical trades can be disbanded")
            assessments = self._active_assessments_for_logical_trades(session, {logical_trade_id})
            return LogicalTradeRegroupPreview(
                affected_assessment_count=len(assessments),
                affected_assessment_labels=tuple(item.assessed_trade_label for item in assessments),
            )

    def create_logical_trade_group(
        self,
        *,
        account_id: int,
        logical_trade_ids: tuple[int, ...],
        display_label: str | None,
    ) -> int:
        """Backward-compatible wrapper for grouping complete logical units."""
        selected = tuple(sorted(set(logical_trade_ids)))
        if len(selected) < 2:
            raise ValueError("Select at least two positions to create one logical trade")
        with self._sessions() as session:
            units = [session.get(LogicalTrade, item_id) for item_id in selected]
            if any(item is None or item.mt5_account_id != account_id for item in units):
                raise ValueError("Selected positions do not belong to this MT5 account")
            position_ids = tuple(
                row.id
                for row in session.scalars(
                    select(Trade).where(Trade.logical_trade_id.in_(selected)).order_by(Trade.entry_time, Trade.id)
                ).all()
            )
        result = self.regroup_logical_trade(
            account_id=account_id,
            position_trade_ids=position_ids,
            display_label=display_label,
            source_logical_trade_ids=selected,
        )
        assert result.logical_trade_id is not None
        return result.logical_trade_id

    def update_logical_trade_group(
        self,
        *,
        account_id: int,
        logical_trade_id: int,
        position_trade_ids: tuple[int, ...],
        display_label: str | None,
    ) -> None:
        """Backward-compatible wrapper for editing a mutable logical trade."""
        self.regroup_logical_trade(
            account_id=account_id,
            logical_trade_id=logical_trade_id,
            position_trade_ids=position_trade_ids,
            display_label=display_label,
        )

    def disband_logical_trade_group(self, *, account_id: int, logical_trade_id: int) -> LogicalTradeRegroupResult:
        """Return every group member to a singleton, superseding any active review."""
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            group = session.get(LogicalTrade, logical_trade_id)
            if group is None or group.mt5_account_id != account_id:
                raise ValueError("Logical trade was not found for this MT5 account")
            rows = session.scalars(select(Trade).where(Trade.logical_trade_id == logical_trade_id)).all()
            if len(rows) < 2:
                raise ValueError("Only grouped logical trades can be disbanded")
            superseded = self._supersede_active_assessments(
                session,
                {logical_trade_id},
                superseded_at=now,
                reason="Logical trade disbanded into individual positions",
            )
            for row in rows:
                singleton = LogicalTrade(mt5_account_id=account_id, display_label=None, created_at=now)
                session.add(singleton)
                session.flush()
                row.logical_trade_id = singleton.id
            self._retire_empty_logical_trades(session, {logical_trade_id})
            return LogicalTradeRegroupResult(None, superseded)

    def _regroup_context(
        self,
        session,  # type: ignore[no-untyped-def]
        *,
        account_id: int,
        position_trade_ids: tuple[int, ...],
        logical_trade_id: int | None,
    ) -> tuple[list[Trade], LogicalTrade | None, list[Trade]]:
        if not position_trade_ids:
            raise ValueError("Select at least one position")
        if logical_trade_id is None and len(position_trade_ids) < 2:
            raise ValueError("Select at least two positions to create one logical trade")
        rows = session.scalars(
            select(Trade).where(Trade.id.in_(position_trade_ids)).order_by(Trade.entry_time, Trade.id)
        ).all()
        if len(rows) != len(position_trade_ids) or any(row.mt5_account_id != account_id for row in rows):
            raise ValueError("Selected positions do not belong to this MT5 account")
        destination = None if logical_trade_id is None else session.get(LogicalTrade, logical_trade_id)
        if destination is not None and destination.mt5_account_id != account_id:
            raise ValueError("Logical trade was not found for this MT5 account")
        if logical_trade_id is not None and destination is None:
            raise ValueError("Logical trade was not found for this MT5 account")
        destination_rows = [] if destination is None else session.scalars(
            select(Trade).where(Trade.logical_trade_id == destination.id).order_by(Trade.entry_time, Trade.id)
        ).all()
        return rows, destination, destination_rows

    @staticmethod
    def _validate_complete_logical_trade_sources(
        session,  # type: ignore[no-untyped-def]
        *,
        account_id: int,
        selected_rows: list[Trade],
        source_logical_trade_ids: tuple[int, ...],
    ) -> None:
        """Reject a stale whole-trade merge before it can move partial groups."""
        if not source_logical_trade_ids:
            return
        source_ids = set(source_logical_trade_ids)
        if len(source_ids) < 2:
            raise ValueError("Select at least two logical trades to create a new logical trade")
        if {row.logical_trade_id for row in selected_rows} != source_ids:
            raise ValueError("Selected logical trades changed. Return to the register and select them again")
        current_rows = session.scalars(
            select(Trade).where(
                Trade.mt5_account_id == account_id,
                Trade.logical_trade_id.in_(source_ids),
            )
        ).all()
        if {row.id for row in current_rows} != {row.id for row in selected_rows}:
            raise ValueError("Selected logical trades changed. Return to the register and select them again")

    @staticmethod
    def _regroup_affected_logical_trade_ids(
        selected_rows: list[Trade],
        destination: LogicalTrade | None,
        destination_rows: list[Trade],
    ) -> set[int]:
        destination_id = None if destination is None else destination.id
        affected = {
            row.logical_trade_id
            for row in selected_rows
            if row.logical_trade_id != destination_id
        }
        if destination_id is not None:
            selected_ids = {row.id for row in selected_rows}
            current_ids = {row.id for row in destination_rows}
            if current_ids != selected_ids:
                affected.add(destination_id)
        return affected

    @staticmethod
    def _active_assessments_for_logical_trades(session, logical_trade_ids: set[int]) -> list[PostTradeAssessment]:  # type: ignore[no-untyped-def]
        if not logical_trade_ids:
            return []
        return session.scalars(
            select(PostTradeAssessment)
            .where(
                PostTradeAssessment.logical_trade_id.in_(logical_trade_ids),
                PostTradeAssessment.superseded_at.is_(None),
            )
            .order_by(PostTradeAssessment.updated_at)
        ).all()

    def _supersede_active_assessments(
        self,
        session,  # type: ignore[no-untyped-def]
        logical_trade_ids: set[int],
        *,
        superseded_at: str,
        reason: str,
        expected_count: int | None = None,
    ) -> int:
        assessments = self._active_assessments_for_logical_trades(session, logical_trade_ids)
        if expected_count is not None and len(assessments) != expected_count:
            raise ValueError("Saved assessments changed. Review the merge warning and confirm again")
        for assessment in assessments:
            assessment.superseded_at = superseded_at
            assessment.superseded_reason = reason
        return len(assessments)

    @staticmethod
    def _retire_empty_logical_trades(session, logical_trade_ids: set[int]) -> None:  # type: ignore[no-untyped-def]
        """Delete empty containers unless an assessment needs them as audit evidence."""
        for logical_trade_id in logical_trade_ids:
            if session.scalar(select(Trade.id).where(Trade.logical_trade_id == logical_trade_id).limit(1)) is not None:
                continue
            if session.scalar(
                select(PostTradeAssessment.id).where(PostTradeAssessment.logical_trade_id == logical_trade_id).limit(1)
            ) is not None:
                continue
            logical_trade = session.get(LogicalTrade, logical_trade_id)
            if logical_trade is not None:
                session.delete(logical_trade)

    def get_post_trade_assessment_for_trade(self, trade_id: int) -> PostTradeAssessmentView | None:
        """Return the trade's single active review, whichever method it was recorded by."""
        with self._sessions() as session:
            row = session.scalar(
                select(PostTradeAssessment).where(
                    PostTradeAssessment.logical_trade_id == trade_id,
                    PostTradeAssessment.superseded_at.is_(None),
                )
            )
            return None if row is None else self._to_post_trade_assessment_view(row)

    def approve_auto_review(self, *, account_id: int, trade_id: int, risk_policy_id: int | None,
                            risk_evidence_source: str, risk_policy_state: str,
                            actual_risk_amount: str | None, criterion_grades: Mapping[str, str]) -> PostTradeAssessmentView:
        grades = self._normalize_criterion_grades(criterion_grades)
        if risk_policy_state not in {"within_policy", "over_policy", "unavailable"}:
            raise ValueError("Unrecognized automatic risk-policy state")
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            trade = session.get(LogicalTrade, trade_id)
            if trade is None or trade.mt5_account_id != account_id:
                raise ValueError("Logical trade was not found for this account")
            row = session.scalar(
                select(PostTradeAssessment).where(
                    PostTradeAssessment.logical_trade_id == trade_id,
                    PostTradeAssessment.superseded_at.is_(None),
                )
            )
            if row is not None and row.method == "manual":
                raise ValueError("This trade already has a full assessment")
            if row is None:
                assessed_position_ids, assessed_trade_label = self._assessment_trade_snapshot(session, trade)
                row = PostTradeAssessment(
                    mt5_account_id=account_id,
                    logical_trade_id=trade_id,
                    method="auto",
                    risk_policy_id=risk_policy_id,
                    risk_evidence_source=risk_evidence_source,
                    risk_policy_state=risk_policy_state,
                    declared_actual_risk_amount=actual_risk_amount,
                    criterion_grades=json.dumps(grades, sort_keys=True),
                    assessed_position_ids=assessed_position_ids,
                    assessed_trade_label=assessed_trade_label,
                    superseded_at=None,
                    superseded_reason=None,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                session.add(row)
                session.flush()
            return self._to_post_trade_assessment_view(row)

    def list_post_trade_assessment_revisions(self, trade_id: int) -> list[PostTradeAssessmentRevisionView]:
        """Return the immutable prior versions of a review, newest first."""
        with self._sessions() as session:
            rows = session.scalars(
                select(PostTradeAssessmentRevision)
                .join(PostTradeAssessment)
                .where(
                    PostTradeAssessment.logical_trade_id == trade_id,
                    PostTradeAssessment.superseded_at.is_(None),
                )
                .order_by(PostTradeAssessmentRevision.version.desc())
            ).all()
            return [self._to_post_trade_assessment_revision_view(row) for row in rows]

    def list_superseded_post_trade_assessments_for_trade(
        self,
        *,
        account_id: int,
        logical_trade_id: int,
    ) -> list[PostTradeAssessmentView]:
        """Archived assessments that covered one or more current member positions."""
        with self._sessions() as session:
            current_rows = session.scalars(
                select(Trade).where(Trade.logical_trade_id == logical_trade_id)
            ).all()
            position_ids = {row.mt5_position_id or "—" for row in current_rows}
            if not position_ids:
                return []
            rows = session.scalars(
                select(PostTradeAssessment)
                .where(
                    PostTradeAssessment.mt5_account_id == account_id,
                    PostTradeAssessment.superseded_at.is_not(None),
                )
                .order_by(PostTradeAssessment.superseded_at.desc())
            ).all()
            return [
                self._to_post_trade_assessment_view(row)
                for row in rows
                if position_ids.intersection(json.loads(row.assessed_position_ids))
            ]

    def list_post_trade_assessment_outcomes(self, account_id: int | None = None) -> list[PostTradeAssessmentOutcome]:
        """Every active approved assessment — Auto and Manual count as reviewed evidence."""
        with self._sessions() as session:
            statement = (
                select(PostTradeAssessment)
                .where(PostTradeAssessment.superseded_at.is_(None), PostTradeAssessment.method.in_(("auto", "manual")))
                .order_by(PostTradeAssessment.updated_at)
            )
            if account_id is not None:
                statement = statement.where(PostTradeAssessment.mt5_account_id == account_id)
            rows = session.scalars(statement).all()
            logical_trades = {item.id: item for item in self._logical_trade_review_items(session, account_id)}
            return [
                PostTradeAssessmentOutcome(self._to_post_trade_assessment_view(assessment), logical_trades[assessment.logical_trade_id])
                for assessment in rows
                if assessment.logical_trade_id in logical_trades
            ]

    def list_active_post_trade_assessments(self, account_id: int) -> list[PostTradeAssessmentView]:
        """Return active assessments without rebuilding the logical-trade read model."""
        with self._sessions() as session:
            rows = session.scalars(
                select(PostTradeAssessment)
                .where(
                    PostTradeAssessment.mt5_account_id == account_id,
                    PostTradeAssessment.superseded_at.is_(None),
                )
                .order_by(PostTradeAssessment.updated_at)
            ).all()
            return [self._to_post_trade_assessment_view(row) for row in rows]

    def save_post_trade_assessment(
        self,
        *,
        account_id: int,
        trade_id: int,
        risk_policy_id: int | None,
        strategy_profile_id: int,
        criterion_grades: Mapping[str, str],
        violation_codes: tuple[str, ...],
        hard_rule_codes: tuple[str, ...],
        declared_actual_risk_amount: str | None,
        post_review_note: str,
        corrective_action: str | None,
        review_context: ReviewContextSelection | None = None,
    ) -> PostTradeAssessmentView:
        """Create or correct the review for one already-imported logical trade."""
        normalized_grades = self._normalize_criterion_grades(criterion_grades)
        normalized_violations = self._normalize_codes(violation_codes, VIOLATION_CODES, "violation")
        normalized_hard_rules = self._normalize_codes(hard_rule_codes, HARD_RULE_CODES, "hard-rule")
        if ("mandatory_setup_absent" in normalized_hard_rules) != ("mandatory_setup_absent" in normalized_violations):
            normalized_violations = tuple(sorted(set(normalized_violations) | {"mandatory_setup_absent"}))
        if "stop_widened" in normalized_hard_rules:
            normalized_violations = tuple(sorted(set(normalized_violations) | {"stop_widened"}))
        if any(grade == "fail" for grade in normalized_grades.values()) and not normalized_violations:
            raise ValueError("Select at least one trading mistake when a criterion fails")
        if (any(grade != "pass" for grade in normalized_grades.values()) or normalized_hard_rules) and not self._optional_text(corrective_action):
            raise ValueError("A corrective action is required for a partial, failed, or hard-rule review")
        actual_risk = None if declared_actual_risk_amount is None or not declared_actual_risk_amount.strip() else _decimal_string(
            self._required_decimal(declared_actual_risk_amount, "Actual risk", minimum=Decimal("0.00000001"))
        )
        review_note = self._required_text(post_review_note, "Post-trade review")
        context = review_context or ReviewContextSelection()
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            trade = session.get(LogicalTrade, trade_id)
            strategy = session.get(StrategyProfile, strategy_profile_id)
            policy = None if risk_policy_id is None else session.get(AccountRiskPolicy, risk_policy_id)
            if trade is None or trade.mt5_account_id != account_id:
                raise ValueError("Logical trade was not found for this account")
            if strategy is None:
                raise ValueError("Strategy profile was not found")
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("Use the strategy assigned to this MT5 account")
            # The placeholder only exists for programmatic/bootstrap callers from
            # earlier releases. The UI never offers it: new accounts must choose a
            # real profile. Promote it on first direct assessment so old test and
            # import helpers still create an account-bound system, rather than
            # reintroducing per-trade strategy selection.
            if account.strategy_profile_id != strategy_profile_id:
                assigned = session.get(StrategyProfile, account.strategy_profile_id)
                if assigned is not None and assigned.name == "Journal default":
                    account.strategy_profile_id = strategy_profile_id
                else:
                    raise ValueError("Use the strategy assigned to this MT5 account")
            if policy is not None and policy.mt5_account_id != account_id:
                raise ValueError("Risk policy does not belong to this account")
            setup_snapshot = self._context_setup_snapshot(session, strategy.id, context.strategy_setup_id)
            session_snapshot = self._context_tag_snapshot(session, "session", context.session_tag_id)
            regime_snapshot = self._context_tag_snapshot(session, "regime", context.regime_tag_id)
            row = session.scalar(
                select(PostTradeAssessment).where(
                    PostTradeAssessment.logical_trade_id == trade_id,
                    PostTradeAssessment.superseded_at.is_(None),
                )
            )
            settings = session.get(FrameworkRuleSettings, 1)
            if settings is None:
                settings = FrameworkRuleSettings(id=1)
                session.add(settings)
                session.flush()
            enabled_hard_rules = self._enabled_hard_rule_codes(settings)
            # Existing effective events remain part of a correction even if
            # the live setting is later disabled. A newly selected event must
            # be enabled now, so the stored code is the auditable snapshot.
            existing_hard_rules = set() if row is None else set(json.loads(row.hard_rule_codes))
            disabled_hard_rules = set(normalized_hard_rules) - enabled_hard_rules - existing_hard_rules
            if disabled_hard_rules:
                raise ValueError("Enable a hard-rule event in Settings → Review rules before recording it on a new assessment")
            if row is None:
                assessed_position_ids, assessed_trade_label = self._assessment_trade_snapshot(session, trade)
                row = PostTradeAssessment(
                    mt5_account_id=account_id,
                    logical_trade_id=trade_id,
                    method="manual",
                    risk_policy_id=risk_policy_id,
                    strategy_profile_id=strategy_profile_id,
                    strategy_snapshot=self._strategy_snapshot_json(strategy),
                    setup_snapshot=setup_snapshot,
                    session_snapshot=session_snapshot,
                    regime_snapshot=regime_snapshot,
                    criterion_grades=json.dumps(normalized_grades, sort_keys=True),
                    violation_codes=json.dumps(normalized_violations),
                    hard_rule_codes=json.dumps(normalized_hard_rules),
                    declared_actual_risk_amount=actual_risk,
                    post_review_note=review_note,
                    corrective_action=self._optional_text(corrective_action),
                    assessed_position_ids=assessed_position_ids,
                    assessed_trade_label=assessed_trade_label,
                    superseded_at=None,
                    superseded_reason=None,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                session.add(row)
            else:
                # A prior "auto" row is upgraded in place rather than superseded-and-recreated,
                # so its automatic-evidence snapshot is preserved in revision history too.
                session.add(
                    PostTradeAssessmentRevision(
                        post_trade_assessment_id=row.id,
                        version=row.version,
                        method=row.method,
                        risk_policy_id=row.risk_policy_id,
                        risk_evidence_source=row.risk_evidence_source,
                        risk_policy_state=row.risk_policy_state,
                        strategy_profile_id=row.strategy_profile_id,
                        strategy_snapshot=row.strategy_snapshot,
                        setup_snapshot=row.setup_snapshot,
                        session_snapshot=row.session_snapshot,
                        regime_snapshot=row.regime_snapshot,
                        criterion_grades=row.criterion_grades,
                        violation_codes=row.violation_codes,
                        hard_rule_codes=row.hard_rule_codes,
                        declared_actual_risk_amount=row.declared_actual_risk_amount,
                        post_review_note=row.post_review_note,
                        corrective_action=row.corrective_action,
                        assessed_position_ids=row.assessed_position_ids,
                        assessed_trade_label=row.assessed_trade_label,
                        archived_at=now,
                    )
                )
                row.method = "manual"
                row.risk_evidence_source = None
                row.risk_policy_state = None
                row.risk_policy_id = risk_policy_id
                row.strategy_profile_id = strategy_profile_id
                row.strategy_snapshot = self._strategy_snapshot_json(strategy)
                row.setup_snapshot = setup_snapshot
                row.session_snapshot = session_snapshot
                row.regime_snapshot = regime_snapshot
                row.criterion_grades = json.dumps(normalized_grades, sort_keys=True)
                row.violation_codes = json.dumps(normalized_violations)
                row.hard_rule_codes = json.dumps(normalized_hard_rules)
                row.declared_actual_risk_amount = actual_risk
                row.post_review_note = review_note
                row.corrective_action = self._optional_text(corrective_action)
                row.updated_at = now
                row.version += 1
            session.flush()
            return self._to_post_trade_assessment_view(row)

    @staticmethod
    def _context_setup_snapshot(session, strategy_profile_id: int, setup_id: int | None) -> str | None:  # type: ignore[no-untyped-def]
        if setup_id is None:
            return None
        row = session.get(StrategySetup, setup_id)
        if row is None or row.strategy_profile_id != strategy_profile_id or not row.active:
            raise ValueError("Choose an active setup from the selected strategy")
        return row.name

    @staticmethod
    def _context_tag_snapshot(session, kind: str, tag_id: int | None) -> str | None:  # type: ignore[no-untyped-def]
        if tag_id is None:
            return None
        row = session.get(ReviewContextTag, tag_id)
        if row is None or row.kind != kind or not row.active:
            raise ValueError(f"Choose an active {kind} tag")
        return row.name

    @staticmethod
    def _normalize_criterion_grades(values: Mapping[str, str]) -> dict[str, str]:
        unknown = set(values) - set(ASSESSMENT_CRITERIA)
        missing = set(ASSESSMENT_CRITERIA) - set(values)
        invalid = {key for key, value in values.items() if value not in ASSESSMENT_GRADES}
        if unknown or missing or invalid:
            raise ValueError("Every three-pillar criterion must be explicitly rated Pass, Partial, or Fail")
        return {key: values[key] for key in ASSESSMENT_CRITERIA}

    @staticmethod
    def _normalize_codes(values: tuple[str, ...], allowed: frozenset[str], label: str) -> tuple[str, ...]:
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown {label} code")
        return tuple(sorted(set(values)))

    @staticmethod
    def _enabled_hard_rule_codes(settings: FrameworkRuleSettings) -> set[str]:
        enabled = {
            "oversized_revenge": settings.oversized_revenge_hard,
            "mandatory_setup_absent": settings.mandatory_setup_hard,
            "stop_widened": settings.stop_widened_hard,
            "shutdown_breach": settings.shutdown_breach_hard,
        }
        return {code for code, active in enabled.items() if active}

    def get_framework_rule_settings(self) -> FrameworkRuleSettingsView:
        with self._sessions.begin() as session:
            row = session.get(FrameworkRuleSettings, 1)
            if row is None:
                row = FrameworkRuleSettings(id=1)
                session.add(row)
                session.flush()
            return self._to_framework_rule_settings_view(row)

    def save_framework_rule_settings(
        self,
        *,
        oversized_revenge_hard: bool,
        mandatory_setup_hard: bool,
        stop_widened_hard: bool,
        shutdown_breach_hard: bool,
        repeated_critical_threshold: int,
    ) -> FrameworkRuleSettingsView:
        if repeated_critical_threshold < 2:
            raise ValueError("Repeated critical violation threshold must be at least two")
        with self._sessions.begin() as session:
            row = session.get(FrameworkRuleSettings, 1)
            if row is None:
                row = FrameworkRuleSettings(id=1)
                session.add(row)
            row.oversized_revenge_hard = oversized_revenge_hard
            row.mandatory_setup_hard = mandatory_setup_hard
            row.stop_widened_hard = stop_widened_hard
            row.shutdown_breach_hard = shutdown_breach_hard
            row.repeated_critical_threshold = repeated_critical_threshold
            session.flush()
            return self._to_framework_rule_settings_view(row)

    def save_framework_period_review(
        self,
        *,
        account_id: int,
        cadence: str,
        period_start: str,
        period_end: str,
        psychology_score: str | None,
        risk_score: str | None,
        system_score: str | None,
        readiness_score: str | None,
        alert_codes: tuple[str, ...],
        recurring_issues: tuple[str, ...],
        review_note: str,
        priority_action: str,
    ) -> FrameworkPeriodReviewView:
        if cadence not in {"weekly", "monthly"}:
            raise ValueError("Period review cadence must be weekly or monthly")
        try:
            start = date.fromisoformat(period_start)
            end = date.fromisoformat(period_end)
        except ValueError as error:
            raise ValueError("Period start and end dates must use YYYY-MM-DD") from error
        if end < start:
            raise ValueError("Period end must not be before its start")
        note = self._required_text(review_note, "Period review")
        action = self._required_text(priority_action, "Priority corrective action")
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None or not account.active:
                raise ValueError("Approved MT5 account was not found")
            row = session.scalar(
                select(FrameworkPeriodReview).where(
                    FrameworkPeriodReview.mt5_account_id == account_id,
                    FrameworkPeriodReview.cadence == cadence,
                    FrameworkPeriodReview.period_start == period_start,
                    FrameworkPeriodReview.period_end == period_end,
                )
            )
            if row is not None:
                # A saved period review snapshots that period's scores/alerts/action;
                # it must never be silently rewritten by a later save.
                raise ValueError("A period review for this period has already been saved and cannot be overwritten.")
            row = FrameworkPeriodReview(
                mt5_account_id=account_id,
                cadence=cadence,
                period_start=period_start,
                period_end=period_end,
                status="reviewed",
                created_at=datetime.now(timezone.utc).isoformat(),
                psychology_score=psychology_score,
                risk_score=risk_score,
                system_score=system_score,
                readiness_score=readiness_score,
                alert_codes=json.dumps(sorted(set(alert_codes))),
                recurring_issues=json.dumps(sorted(set(recurring_issues))),
                review_note=note,
                priority_action=action,
            )
            session.add(row)
            session.flush()
            return self._to_framework_period_review_view(row)

    def skip_framework_period_review(
        self,
        *,
        account_id: int,
        cadence: str,
        period_start: str,
        period_end: str,
        reason: str,
    ) -> FrameworkPeriodReviewView:
        """Persist an explicit skipped disposition for one completed active period."""
        if cadence not in {"weekly", "monthly"}:
            raise ValueError("Period review cadence must be weekly or monthly")
        skip_reason = self._required_text(reason, "Skip reason")
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None or not account.active:
                raise ValueError("Approved MT5 account was not found")
            existing = session.scalar(
                select(FrameworkPeriodReview).where(
                    FrameworkPeriodReview.mt5_account_id == account_id,
                    FrameworkPeriodReview.cadence == cadence,
                    FrameworkPeriodReview.period_start == period_start,
                    FrameworkPeriodReview.period_end == period_end,
                )
            )
            if existing is not None:
                raise ValueError("This period already has a reviewed or skipped disposition.")
            row = FrameworkPeriodReview(
                mt5_account_id=account_id,
                cadence=cadence,
                period_start=period_start,
                period_end=period_end,
                status="skipped",
                created_at=datetime.now(timezone.utc).isoformat(),
                psychology_score=None,
                risk_score=None,
                system_score=None,
                readiness_score=None,
                alert_codes="[]",
                recurring_issues="[]",
                review_note=skip_reason,
                priority_action="—",
            )
            session.add(row)
            session.flush()
            return self._to_framework_period_review_view(row)

    def list_framework_period_reviews(self, account_id: int, cadence: str | None = None) -> list[FrameworkPeriodReviewView]:
        with self._sessions() as session:
            statement = select(FrameworkPeriodReview).where(FrameworkPeriodReview.mt5_account_id == account_id)
            if cadence is not None:
                statement = statement.where(FrameworkPeriodReview.cadence == cadence)
            rows = session.scalars(statement.order_by(FrameworkPeriodReview.period_end.desc(), FrameworkPeriodReview.id.desc())).all()
            return [self._to_framework_period_review_view(row) for row in rows]

    def list_pillar_roadmap_evidence(self, account_id: int) -> list[PillarRoadmapEvidenceView]:
        with self._sessions() as session:
            account_scope = self._roadmap_scope_key(account_id)
            rows = session.scalars(
                select(PillarRoadmapEvidence)
                .where(PillarRoadmapEvidence.scope_key == account_scope)
                .order_by(PillarRoadmapEvidence.pillar, PillarRoadmapEvidence.level, PillarRoadmapEvidence.item_key)
            ).all()
            return [PillarRoadmapEvidenceView(row.scope_key, row.pillar, row.level, row.item_key, row.completed, row.evidence_note, row.updated_at) for row in rows]

    def get_active_framework_focus(self, account_id: int) -> FrameworkFocusView | None:
        with self._sessions() as session:
            row = session.scalar(select(FrameworkFocus).where(FrameworkFocus.account_id == account_id, FrameworkFocus.status == "active"))
            return None if row is None else self._to_framework_focus_view(row)

    def list_framework_focuses(self, account_id: int) -> list[FrameworkFocusView]:
        with self._sessions() as session:
            rows = session.scalars(
                select(FrameworkFocus)
                .where(FrameworkFocus.account_id == account_id)
                .order_by(FrameworkFocus.created_at.desc(), FrameworkFocus.id.desc())
            ).all()
            return [self._to_framework_focus_view(row) for row in rows]

    def save_framework_focus(
        self, *, account_id: int, pillar: str, metric_kind: str, metric_code: str | None,
        hypothesis: str, action_text: str, baseline_value: str | None, target_value: str, target_reviews: int,
        starting_manual_reviews: int, source: str = "manual", coach_reason: str | None = None,
    ) -> FrameworkFocusView:
        if pillar not in {"psychology", "risk", "system"}:
            raise ValueError("Unknown framework pillar")
        if metric_kind not in {"manual_evidence", "criterion", "component", "violation"}:
            raise ValueError("Unknown framework focus metric")
        if metric_kind == "manual_evidence" and metric_code is not None:
            raise ValueError("Manual-evidence focus cannot use a metric code")
        if metric_kind != "manual_evidence" and not metric_code:
            raise ValueError("Choose a metric for this framework focus")
        if target_reviews not in {5, 10, 20}:
            raise ValueError("Focus sample must be 5, 10, or 20 reviewed trades")
        if source not in {"manual", "coach"}:
            raise ValueError("Unknown framework focus source")
        if account_id is None:
            raise ValueError("A framework focus needs an account")
        with self._sessions.begin() as session:
            if session.scalar(select(FrameworkFocus).where(FrameworkFocus.account_id == account_id, FrameworkFocus.status == "active")) is not None:
                raise ValueError("Resolve the active framework focus before starting another")
            row = FrameworkFocus(
                scope_key="trader", account_id=account_id, pillar=pillar,
                metric_kind=metric_kind, metric_code=metric_code, hypothesis=self._required_text(hypothesis, "Focus hypothesis"),
                action_text=self._required_text(action_text, "Focus action"), baseline_value=baseline_value,
                target_value=target_value, target_reviews=target_reviews, starting_manual_reviews=starting_manual_reviews,
                status="active", source=source, coach_reason=self._optional_text(coach_reason), resolution_note=None,
                created_at=datetime.now(timezone.utc).isoformat(), resolved_at=None,
            )
            session.add(row)
            session.flush()
            return self._to_framework_focus_view(row)

    def resolve_framework_focus(self, *, focus_id: int, outcome: str, resolution_note: str) -> FrameworkFocusView:
        if outcome not in {"completed", "abandoned", "superseded"}:
            raise ValueError("Focus outcome must be completed, abandoned, or superseded")
        with self._sessions.begin() as session:
            row = session.get(FrameworkFocus, focus_id)
            if row is None or row.status != "active":
                raise ValueError("Active framework focus was not found")
            row.status, row.resolution_note, row.resolved_at = outcome, self._required_text(resolution_note, "Focus reflection"), datetime.now(timezone.utc).isoformat()
            session.flush()
            return self._to_framework_focus_view(row)

    def update_framework_focus_action(self, *, focus_id: int, action_text: str) -> FrameworkFocusView:
        with self._sessions.begin() as session:
            row = session.get(FrameworkFocus, focus_id)
            if row is None or row.status != "active":
                raise ValueError("Active framework focus was not found")
            row.action_text = self._required_text(action_text, "Focus action")
            session.flush()
            return self._to_framework_focus_view(row)

    @staticmethod
    def _to_framework_focus_view(row: FrameworkFocus) -> FrameworkFocusView:
        return FrameworkFocusView(
            row.id, row.account_id, row.pillar, row.metric_kind, row.metric_code, row.hypothesis,
            row.action_text, row.baseline_value, row.target_value, row.target_reviews,
            row.starting_manual_reviews, row.status, row.source, row.coach_reason, row.resolution_note, row.created_at, row.resolved_at,
        )

    def save_pillar_roadmap_evidence(
        self, *, account_id: int, pillar: str, level: int, item_key: str, completed: bool, evidence_note: str | None
    ) -> PillarRoadmapEvidenceView:
        if pillar not in {"psychology", "risk", "system"} or level not in {1, 2, 3, 4, 5}:
            raise ValueError("Unknown pillar roadmap item")
        if completed and not self._optional_text(evidence_note):
            raise ValueError("An evidence note is required before completing a roadmap item")
        if account_id is None:
            raise ValueError("An account is required for roadmap evidence")
        scope_key = self._roadmap_scope_key(account_id)
        with self._sessions.begin() as session:
            row = session.scalar(
                select(PillarRoadmapEvidence).where(
                    PillarRoadmapEvidence.scope_key == scope_key,
                    PillarRoadmapEvidence.pillar == pillar,
                    PillarRoadmapEvidence.level == level,
                    PillarRoadmapEvidence.item_key == item_key,
                )
            )
            if row is None:
                row = PillarRoadmapEvidence(scope_key=scope_key, pillar=pillar, level=level, item_key=item_key, completed=completed, evidence_note=self._optional_text(evidence_note), updated_at=datetime.now(timezone.utc).isoformat())
                session.add(row)
            else:
                row.completed = completed
                row.evidence_note = self._optional_text(evidence_note)
                row.updated_at = datetime.now(timezone.utc).isoformat()
            session.flush()
            return PillarRoadmapEvidenceView(row.scope_key, row.pillar, row.level, row.item_key, row.completed, row.evidence_note, row.updated_at)

    @staticmethod
    def _roadmap_scope_key(account_id: int) -> str:
        return f"account:{account_id}"

    def latest_mt5_import_hash(self, *, login: str, broker_server: str, source_file_path: str) -> str | None:
        with self._sessions() as session:
            account = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server))
            if account is None:
                return None
            return session.scalar(
                select(MT5ImportRun.source_file_hash)
                .where(MT5ImportRun.mt5_account_id == account.id, MT5ImportRun.source_file_path == source_file_path, MT5ImportRun.status == "succeeded")
                .order_by(MT5ImportRun.id.desc())
                .limit(1)
            )

    def latest_mt5_import_fingerprint(
        self, *, login: str, broker_server: str, source_file_path: str
    ) -> tuple[str, int | None, int | None] | None:
        with self._sessions() as session:
            account = session.scalar(
                select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server)
            )
            if account is None:
                return None
            row = session.execute(
                select(
                    MT5ImportRun.source_file_hash,
                    MT5ImportRun.source_file_mtime_ns,
                    MT5ImportRun.source_file_size,
                )
                .where(
                    MT5ImportRun.mt5_account_id == account.id,
                    MT5ImportRun.source_file_path == source_file_path,
                    MT5ImportRun.status == "succeeded",
                )
                .order_by(MT5ImportRun.id.desc())
                .limit(1)
            ).one_or_none()
            return None if row is None else (row[0], row[1], row[2])

    def latest_ingestion_import(self, account_id: int) -> tuple[str, int, int] | None:
        """Most recent successful MT5ImportRun written by the ingestion API (POST /ingest) for this account."""
        with self._sessions() as session:
            row = session.execute(
                select(MT5ImportRun.created_at, MT5ImportRun.created_count, MT5ImportRun.updated_count)
                .where(
                    MT5ImportRun.mt5_account_id == account_id,
                    MT5ImportRun.source_file_path.like("http:%"),
                    MT5ImportRun.status == "succeeded",
                )
                .order_by(MT5ImportRun.id.desc())
                .limit(1)
            ).one_or_none()
            return None if row is None else (row[0], row[1], row[2])

    def save_strategy_profile(
        self,
        *,
        name: str,
        description: str | None,
        backtest_verified: bool = False,
        backtest_notes: str | None = None,
        magic_numbers: str | None | object = _UNSET,
        strategy_id: int | None = None,
    ) -> StrategyProfileView:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise ValueError("Strategy name is required")
        if len(clean_name) > 100:
            raise ValueError("Strategy name must be 100 characters or fewer")

        normalized_name = normalize_strategy_name(clean_name)

        with self._sessions.begin() as session:
            profile = session.get(StrategyProfile, strategy_id) if strategy_id is not None else None
            if strategy_id is not None and profile is None:
                raise ValueError("Strategy profile was not found")
            matching_profile = session.scalar(select(StrategyProfile).where(StrategyProfile.normalized_name == normalized_name))
            if matching_profile is not None and (profile is None or matching_profile.id != profile.id):
                raise ValueError("A strategy with this name already exists")
            if profile is None:
                profile = StrategyProfile(name=clean_name, normalized_name=normalized_name)
                session.add(profile)
            session.flush()
            parsed_magic_numbers = (
                self._magic_numbers_for_profile(session, profile.id)
                if magic_numbers is _UNSET
                else self._parse_magic_numbers(magic_numbers if isinstance(magic_numbers, str) else None)
            )
            profile.name = clean_name
            profile.description = self._optional_text(description)
            profile.backtest_verified = backtest_verified
            profile.backtest_notes = self._optional_text(backtest_notes)
            conflicts = session.scalars(
                select(StrategyMagicNumber).where(StrategyMagicNumber.magic_number.in_(parsed_magic_numbers))
            ).all() if parsed_magic_numbers else []
            if any(item.strategy_profile_id != profile.id for item in conflicts):
                raise ValueError("An MT5 magic number is already assigned to another strategy")
            for item in session.scalars(
                select(StrategyMagicNumber).where(StrategyMagicNumber.strategy_profile_id == profile.id)
            ).all():
                session.delete(item)
            for magic_number in parsed_magic_numbers:
                session.add(StrategyMagicNumber(strategy_profile_id=profile.id, magic_number=magic_number))
            session.flush()
            return self._to_strategy_profile_view(profile, parsed_magic_numbers)

    def get_strategy_profile(self, name: str) -> StrategyProfileView | None:
        with self._sessions() as session:
            profile = session.scalar(select(StrategyProfile).where(StrategyProfile.normalized_name == normalize_strategy_name(name)))
            return None if profile is None else self._to_strategy_profile_view(profile, self._magic_numbers_for_profile(session, profile.id))

    def list_strategy_profiles(self) -> list[StrategyProfileView]:
        with self._sessions() as session:
            profiles = session.scalars(select(StrategyProfile).order_by(StrategyProfile.name)).all()
            magic_numbers = self._magic_numbers_by_profile(session)
            return [self._to_strategy_profile_view(profile, magic_numbers.get(profile.id, ())) for profile in profiles]

    def delete_strategy_profile(self, strategy_id: int) -> None:
        """Permanently delete a strategy with no MT5 account currently bound to it."""
        with self._sessions.begin() as session:
            strategy = session.get(StrategyProfile, strategy_id)
            if strategy is None:
                raise ValueError("The selected strategy no longer exists")
            if strategy.name == "Journal default":
                raise ValueError("The journal default strategy cannot be deleted")
            if session.scalar(select(MT5Account.id).where(MT5Account.strategy_profile_id == strategy_id).limit(1)) is not None:
                raise ValueError("A strategy bound to an account cannot be deleted")
            settings = session.get(JournalSettings, 1)
            if settings is not None and settings.default_strategy_profile_id == strategy_id:
                settings.default_strategy_profile_id = None
                settings.default_strategy_name = None
            session.execute(delete(StrategyMagicNumber).where(StrategyMagicNumber.strategy_profile_id == strategy_id))
            session.execute(delete(StrategySetup).where(StrategySetup.strategy_profile_id == strategy_id))
            session.flush()
            session.delete(strategy)

    def list_strategy_setups(self, strategy_profile_id: int, *, include_inactive: bool = False) -> list[StrategySetupView]:
        with self._sessions() as session:
            statement = select(StrategySetup).where(StrategySetup.strategy_profile_id == strategy_profile_id)
            if not include_inactive:
                statement = statement.where(StrategySetup.active.is_(True))
            rows = session.scalars(statement.order_by(StrategySetup.name)).all()
            return [StrategySetupView(row.id, row.strategy_profile_id, row.name, row.description, row.active) for row in rows]

    def save_strategy_setup(
        self, *, strategy_profile_id: int, name: str, description: str | None = None, setup_id: int | None = None, active: bool = True
    ) -> StrategySetupView:
        clean_name = self._required_text(name, "Setup name")
        normalized = normalize_strategy_name(clean_name)
        with self._sessions.begin() as session:
            if session.get(StrategyProfile, strategy_profile_id) is None:
                raise ValueError("Strategy profile was not found")
            row = session.get(StrategySetup, setup_id) if setup_id is not None else None
            if setup_id is not None and (row is None or row.strategy_profile_id != strategy_profile_id):
                raise ValueError("Strategy setup was not found")
            duplicate = session.scalar(select(StrategySetup).where(StrategySetup.strategy_profile_id == strategy_profile_id, StrategySetup.normalized_name == normalized))
            if duplicate is not None and (row is None or duplicate.id != row.id):
                raise ValueError("This strategy already has a setup with that name")
            if row is None:
                row = StrategySetup(strategy_profile_id=strategy_profile_id, name=clean_name, normalized_name=normalized)
                session.add(row)
            row.name, row.normalized_name, row.description, row.active = clean_name, normalized, self._optional_text(description), active
            session.flush()
            return StrategySetupView(row.id, row.strategy_profile_id, row.name, row.description, row.active)

    def list_review_context_tags(self, kind: str, *, include_inactive: bool = False) -> list[ReviewContextTagView]:
        if kind not in {"session", "regime"}:
            raise ValueError("Review context kind must be session or regime")
        with self._sessions() as session:
            statement = select(ReviewContextTag).where(ReviewContextTag.kind == kind)
            if not include_inactive:
                statement = statement.where(ReviewContextTag.active.is_(True))
            rows = session.scalars(statement.order_by(ReviewContextTag.name)).all()
            return [ReviewContextTagView(row.id, row.kind, row.name, row.active) for row in rows]

    def save_review_context_tag(self, *, kind: str, name: str, tag_id: int | None = None, active: bool = True) -> ReviewContextTagView:
        if kind not in {"session", "regime"}:
            raise ValueError("Review context kind must be session or regime")
        clean_name = self._required_text(name, f"{kind.capitalize()} name")
        normalized = normalize_strategy_name(clean_name)
        with self._sessions.begin() as session:
            row = session.get(ReviewContextTag, tag_id) if tag_id is not None else None
            if tag_id is not None and (row is None or row.kind != kind):
                raise ValueError("Review context tag was not found")
            duplicate = session.scalar(select(ReviewContextTag).where(ReviewContextTag.kind == kind, ReviewContextTag.normalized_name == normalized))
            if duplicate is not None and (row is None or duplicate.id != row.id):
                raise ValueError(f"This {kind} already exists")
            if row is None:
                row = ReviewContextTag(kind=kind, name=clean_name, normalized_name=normalized)
                session.add(row)
            row.name, row.normalized_name, row.active = clean_name, normalized, active
            session.flush()
            return ReviewContextTagView(row.id, row.kind, row.name, row.active)

    def find_strategy_profile_by_magic_number(self, magic_number: str | None) -> StrategyProfileView | None:
        if magic_number is None or magic_number == "0":
            return None
        with self._sessions() as session:
            row = session.scalar(
                select(StrategyProfile)
                .join(StrategyMagicNumber)
                .where(StrategyMagicNumber.magic_number == magic_number)
            )
            return None if row is None else self._to_strategy_profile_view(row, self._magic_numbers_for_profile(session, row.id))

    def set_default_strategy(self, strategy_id: int | str | None) -> None:
        with self._sessions.begin() as session:
            settings = session.get(JournalSettings, 1)
            if settings is None:
                raise RuntimeError("Configure journal settings before choosing a default strategy")
            if strategy_id is None:
                settings.default_strategy_name = None
                settings.default_strategy_profile_id = None
                return
            profile = session.get(StrategyProfile, strategy_id) if isinstance(strategy_id, int) else session.scalar(
                select(StrategyProfile).where(StrategyProfile.normalized_name == normalize_strategy_name(strategy_id))
            )
            if profile is None:
                raise ValueError("Save the strategy profile before making it the default")
            settings.default_strategy_name = profile.name
            settings.default_strategy_profile_id = profile.id

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{label} is required")
        return cleaned

    @staticmethod
    def _parse_magic_numbers(value: str | None) -> tuple[str, ...]:
        if value is None or not value.strip():
            return ()
        numbers = tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))
        if not numbers or any(not item.isdecimal() for item in numbers):
            raise ValueError("MT5 magic numbers must be comma-separated whole numbers")
        return numbers

    @staticmethod
    def _magic_numbers_for_profile(session, profile_id: int) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
        return tuple(
            session.scalars(
                select(StrategyMagicNumber.magic_number)
                .where(StrategyMagicNumber.strategy_profile_id == profile_id)
                .order_by(StrategyMagicNumber.magic_number)
            ).all()
        )

    @staticmethod
    def _magic_numbers_by_profile(session) -> dict[int, tuple[str, ...]]:  # type: ignore[no-untyped-def]
        values: dict[int, list[str]] = {}
        for profile_id, magic_number in session.execute(
            select(StrategyMagicNumber.strategy_profile_id, StrategyMagicNumber.magic_number).order_by(StrategyMagicNumber.magic_number)
        ).all():
            values.setdefault(profile_id, []).append(magic_number)
        return {profile_id: tuple(numbers) for profile_id, numbers in values.items()}

    @staticmethod
    def _required_decimal(value: str, label: str, minimum: Decimal | None = None, maximum: Decimal | None = None) -> Decimal:
        try:
            decimal_value = Decimal(value.strip())
        except ArithmeticError as error:
            raise ValueError(f"{label} must be a number") from error
        if not decimal_value.is_finite():
            raise ValueError(f"{label} must be a finite number")
        if minimum is not None and decimal_value < minimum:
            raise ValueError(f"{label} must be at least {minimum}")
        if maximum is not None and decimal_value > maximum:
            raise ValueError(f"{label} must be no more than {maximum}")
        return decimal_value

    @staticmethod
    def _to_strategy_profile_view(profile: StrategyProfile, magic_numbers: tuple[str, ...] = ()) -> StrategyProfileView:
        return StrategyProfileView(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            backtest_verified=profile.backtest_verified,
            backtest_notes=profile.backtest_notes,
            magic_numbers=magic_numbers,
        )

    @staticmethod
    def _to_risk_policy_view(policy: AccountRiskPolicy) -> AccountRiskPolicyView:
        return AccountRiskPolicyView(
            policy.id,
            policy.mt5_account_id,
            policy.version,
            policy.risk_per_trade_percent,
            policy.maximum_risk_per_trade_percent,
            policy.daily_loss_limit_r,
            policy.weekly_loss_limit_r,
            policy.max_drawdown_percent,
            policy.drawdown_reset_period,
            policy.max_open_risk_r,
            policy.max_consecutive_losses,
            policy.loss_streak_reset_period,
            policy.minimum_rr,
            policy.correlation_policy,
            policy.pretrade_balance_auto_evidence_enabled,
            policy.server_utc_offset_minutes,
            policy.created_at,
        )

    def _logical_trade_review_items(self, session, account_id: int | None = None) -> list[ClosedTradeReviewItem]:  # type: ignore[no-untyped-def]
        statement = select(LogicalTrade)
        if account_id is not None:
            statement = statement.where(LogicalTrade.mt5_account_id == account_id)
        logical_rows = session.scalars(statement.order_by(LogicalTrade.id)).all()
        logical_ids = [row.id for row in logical_rows]
        if not logical_ids:
            return []
        raw_rows = session.scalars(
            select(Trade).where(Trade.logical_trade_id.in_(logical_ids)).order_by(Trade.entry_time, Trade.exit_time, Trade.id)
        ).all()
        members_by_logical: dict[int, list[ImportedPositionReviewItem]] = {item_id: [] for item_id in logical_ids}
        for row in raw_rows:
            members_by_logical[row.logical_trade_id].append(self._to_imported_position_review_item(row))
        return [
            self._to_closed_trade_review_item(row, tuple(members_by_logical[row.id]))
            for row in logical_rows
            if members_by_logical[row.id]
        ]

    def _assessment_trade_snapshot(self, session, logical_trade: LogicalTrade) -> tuple[str, str]:  # type: ignore[no-untyped-def]
        """Freeze the member IDs and label that a completed review assessed."""
        rows = session.scalars(
            select(Trade).where(Trade.logical_trade_id == logical_trade.id).order_by(Trade.entry_time, Trade.exit_time, Trade.id)
        ).all()
        if not rows:
            raise ValueError("Logical trade has no imported positions to assess")
        item = self._to_closed_trade_review_item(
            logical_trade,
            tuple(self._to_imported_position_review_item(row) for row in rows),
        )
        return json.dumps(item.position_ids), item.display_label

    @staticmethod
    def _to_imported_position_review_item(row: Trade) -> ImportedPositionReviewItem:
        return ImportedPositionReviewItem(
            id=row.id,
            account_id=row.mt5_account_id or 0,
            logical_trade_id=row.logical_trade_id,
            position_id=row.mt5_position_id,
            symbol=row.symbol,
            direction=row.direction,
            entry_time=row.entry_time,
            exit_time=row.exit_time,
            server_utc_offset_minutes=row.server_utc_offset_minutes,
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            volume=row.volume,
            net_pnl=row.net_pnl,
            entry_stop_price=row.entry_stop_price,
            entry_target_price=row.entry_target_price,
            close_stop_price=row.close_stop_price,
            entry_magic_number=row.entry_magic_number,
            entry_deal_count=row.entry_deal_count,
            exit_reason=row.exit_reason,
            initial_risk_amount=row.initial_risk_amount,
            initial_reward_amount=row.initial_reward_amount,
            pretrade_account_balance=row.pretrade_account_balance,
            auto_risk_policy_id=row.auto_risk_policy_id,
        )

    @staticmethod
    def _to_closed_trade_review_item(row: LogicalTrade, members: tuple[ImportedPositionReviewItem, ...]) -> ClosedTradeReviewItem:
        ordered = tuple(sorted(members, key=lambda item: (item.entry_time, item.id)))
        latest = max(ordered, key=lambda item: (item.exit_time, item.id))
        total_volume = sum((Decimal(item.volume) for item in ordered), Decimal("0"))
        entry_notional = sum((Decimal(item.entry_price) * Decimal(item.volume) for item in ordered), Decimal("0"))
        exit_notional = sum((Decimal(item.exit_price) * Decimal(item.volume) for item in ordered), Decimal("0"))

        def common(field: str) -> str | None:
            values = {getattr(item, field) for item in ordered}
            return values.pop() if len(values) == 1 else None

        def all_sum(field: str) -> str | None:
            values = [getattr(item, field) for item in ordered]
            if any(value is None for value in values):
                return None
            return _decimal_string(sum((Decimal(value) for value in values if value is not None), Decimal("0")))

        entry_time = ordered[0].entry_time
        generated = f"{ordered[0].symbol} {ordered[0].direction} · {entry_time[:16].replace('T', ' ')}"
        display_label = row.display_label or (f"#{ordered[0].position_id or '—'}" if len(ordered) == 1 else generated)
        return ClosedTradeReviewItem(
            id=row.id,
            position_id=ordered[0].position_id if len(ordered) == 1 else None,
            position_ids=tuple(item.position_id or "—" for item in ordered),
            custom_label=row.display_label,
            display_label=display_label,
            members=ordered,
            symbol=ordered[0].symbol,
            direction=ordered[0].direction,
            entry_time=entry_time,
            exit_time=latest.exit_time,
            server_utc_offset_minutes=latest.server_utc_offset_minutes,
            entry_price=_decimal_string(entry_notional / total_volume),
            exit_price=_decimal_string(exit_notional / total_volume),
            volume=_decimal_string(total_volume),
            net_pnl=_decimal_string(sum((Decimal(item.net_pnl) for item in ordered), Decimal("0"))),
            entry_stop_price=common("entry_stop_price"),
            entry_target_price=common("entry_target_price"),
            close_stop_price=common("close_stop_price"),
            entry_magic_number=common("entry_magic_number"),
            entry_deal_count=sum((item.entry_deal_count or 0 for item in ordered), 0) or None,
            exit_reason=common("exit_reason"),
            initial_risk_amount=all_sum("initial_risk_amount"),
            initial_reward_amount=all_sum("initial_reward_amount"),
            auto_risk_policy_id=common("auto_risk_policy_id"),
        )

    @staticmethod
    def _validate_group_members(rows: list[Trade], account_id: int) -> None:
        if len(rows) < 2 or any(row.mt5_account_id != account_id for row in rows):
            raise ValueError("Select at least two positions from this MT5 account")
        symbols = {row.symbol for row in rows}
        directions = {row.direction for row in rows}
        policies = {row.auto_risk_policy_id for row in rows}
        if len(symbols) != 1 or len(directions) != 1:
            raise ValueError("Grouped positions must use the same symbol and direction")
        if len(policies) != 1:
            raise ValueError("Grouped positions must use the same imported Risk-policy version")

    @staticmethod
    def _to_post_trade_assessment_view(row: PostTradeAssessment) -> PostTradeAssessmentView:
        return PostTradeAssessmentView(
            id=row.id,
            account_id=row.mt5_account_id,
            trade_id=row.logical_trade_id,
            method=row.method,
            risk_policy_id=row.risk_policy_id,
            risk_evidence_source=row.risk_evidence_source,
            risk_policy_state=row.risk_policy_state,
            strategy_profile_id=row.strategy_profile_id,
            strategy_snapshot=None if row.strategy_snapshot is None else SQLiteJournalRepository._strategy_snapshot_from_json(row.strategy_snapshot),
            setup_snapshot=row.setup_snapshot,
            session_snapshot=row.session_snapshot,
            regime_snapshot=row.regime_snapshot,
            criterion_grades=dict(json.loads(row.criterion_grades)),
            violation_codes=tuple(json.loads(row.violation_codes)),
            hard_rule_codes=tuple(json.loads(row.hard_rule_codes)),
            declared_actual_risk_amount=row.declared_actual_risk_amount,
            post_review_note=row.post_review_note,
            corrective_action=row.corrective_action,
            assessed_position_ids=tuple(json.loads(row.assessed_position_ids)),
            assessed_trade_label=row.assessed_trade_label,
            superseded_at=row.superseded_at,
            superseded_reason=row.superseded_reason,
            created_at=row.created_at,
            updated_at=row.updated_at,
            version=row.version,
        )

    @staticmethod
    def _to_post_trade_assessment_revision_view(row: PostTradeAssessmentRevision) -> PostTradeAssessmentRevisionView:
        return PostTradeAssessmentRevisionView(
            version=row.version,
            method=row.method,
            risk_policy_id=row.risk_policy_id,
            risk_evidence_source=row.risk_evidence_source,
            risk_policy_state=row.risk_policy_state,
            strategy_profile_id=row.strategy_profile_id,
            strategy_snapshot=None if row.strategy_snapshot is None else SQLiteJournalRepository._strategy_snapshot_from_json(row.strategy_snapshot),
            setup_snapshot=row.setup_snapshot,
            session_snapshot=row.session_snapshot,
            regime_snapshot=row.regime_snapshot,
            criterion_grades=dict(json.loads(row.criterion_grades)),
            violation_codes=tuple(json.loads(row.violation_codes)),
            hard_rule_codes=tuple(json.loads(row.hard_rule_codes)),
            declared_actual_risk_amount=row.declared_actual_risk_amount,
            post_review_note=row.post_review_note,
            corrective_action=row.corrective_action,
            assessed_position_ids=() if row.assessed_position_ids is None else tuple(json.loads(row.assessed_position_ids)),
            assessed_trade_label=row.assessed_trade_label,
            archived_at=row.archived_at,
        )

    @staticmethod
    def _to_framework_rule_settings_view(row: FrameworkRuleSettings) -> FrameworkRuleSettingsView:
        return FrameworkRuleSettingsView(
            row.oversized_revenge_hard,
            row.mandatory_setup_hard,
            row.stop_widened_hard,
            row.shutdown_breach_hard,
            row.repeated_critical_threshold,
        )

    @staticmethod
    def _to_framework_period_review_view(row: FrameworkPeriodReview) -> FrameworkPeriodReviewView:
        return FrameworkPeriodReviewView(
            row.id,
            row.mt5_account_id,
            row.cadence,
            row.period_start,
            row.period_end,
            row.status,
            row.psychology_score,
            row.risk_score,
            row.system_score,
            row.readiness_score,
            tuple(json.loads(row.alert_codes)),
            tuple(json.loads(row.recurring_issues)),
            row.review_note,
            row.priority_action,
            row.created_at,
        )

    @staticmethod
    def _strategy_snapshot_json(profile: StrategyProfile) -> str:
        return json.dumps(
            {
                "profile_id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "backtest_verified": profile.backtest_verified,
                "backtest_notes": profile.backtest_notes,
            },
            sort_keys=True,
        )

    @staticmethod
    def _strategy_snapshot_from_json(value: str) -> StrategyEvidenceSnapshot:
        payload = json.loads(value)
        return StrategyEvidenceSnapshot(
            profile_id=payload["profile_id"],
            name=payload["name"],
            description=payload.get("description"),
            backtest_verified=bool(payload.get("backtest_verified")),
            backtest_notes=payload.get("backtest_notes"),
        )

    def list_trades(self) -> list[TradeListItem]:
        with self._sessions() as session:
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            account_strategies = {
                account.id: account.strategy_profile_id
                for account in session.scalars(select(MT5Account)).all()
            }
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            trades = session.scalars(select(Trade).order_by(Trade.exit_time.desc())).all()
            return [
                self._to_trade_list_item(
                    trade,
                    profiles_by_id,
                    account_strategies,
                    policies_by_id,
                    active_policies_by_account,
                    funded_capital_by_account,
                )
                for trade in trades
            ]

    @staticmethod
    def _to_trade_list_item(
        trade: Trade,
        profiles_by_id: dict[int, StrategyProfile],
        account_strategies: dict[int, int],
        policies_by_id: dict[int, AccountRiskPolicyView],
        active_policies_by_account: dict[int, AccountRiskPolicyView],
        funded_capital_by_account: dict[int, str | None],
    ) -> TradeListItem:
        effective_risk, risk_source = SQLiteJournalRepository._standard_risk_for_trade(
            trade,
            policies_by_id,
            active_policies_by_account,
            funded_capital_by_account,
        )
        result_r = None if effective_risk is None else _decimal_string(Decimal(trade.net_pnl) / Decimal(effective_risk))
        strategy_id = account_strategies.get(trade.mt5_account_id)
        strategy = profiles_by_id[strategy_id].name if strategy_id in profiles_by_id else None
        strategy_source = "Account"
        return TradeListItem(trade.source, trade.mt5_position_id, trade.symbol, trade.direction, trade.exit_time, trade.net_pnl, result_r, strategy, strategy_source, effective_risk, risk_source)

    @staticmethod
    def _risk_reporting_context(session) -> tuple[dict[int, AccountRiskPolicyView], dict[int, AccountRiskPolicyView], dict[int, str | None]]:  # type: ignore[no-untyped-def]
        policy_rows = session.scalars(select(AccountRiskPolicy)).all()
        policies = [SQLiteJournalRepository._to_risk_policy_view(policy) for policy in policy_rows]
        policies_by_id = {policy.id: policy for policy in policies}
        active_policies_by_account = {
            policy_row.mt5_account_id: policies_by_id[policy_row.id]
            for policy_row in policy_rows
            if policy_row.active
        }
        funded_capital_by_account = {
            account.id: account.opening_balance
            for account in session.scalars(select(MT5Account)).all()
        }
        return policies_by_id, active_policies_by_account, funded_capital_by_account

    @staticmethod
    def _standard_risk_for_trade(
        trade: Trade,
        policies_by_id: dict[int, AccountRiskPolicyView],
        active_policies_by_account: dict[int, AccountRiskPolicyView],
        funded_capital_by_account: dict[int, str | None],
    ) -> tuple[str | None, str]:
        if trade.mt5_account_id is None:
            return None, "Awaiting account risk policy"
        policy = policies_by_id.get(trade.auto_risk_policy_id) or active_policies_by_account.get(trade.mt5_account_id)
        funded_capital = funded_capital_by_account.get(trade.mt5_account_id)
        if policy is None or funded_capital is None:
            return None, "Awaiting account risk policy"
        amount = Decimal(funded_capital) * Decimal(policy.standard_risk_per_trade_percent) / Decimal("100")
        if amount <= 0:
            return None, "Awaiting account risk policy"
        return _decimal_string(amount), f"Risk policy v{policy.version} standard risk"

    def list_trade_performance(self, account_id: int | None = None) -> list[TradePerformanceItem]:
        with self._sessions() as session:
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            account_strategies = {
                account.id: account.strategy_profile_id
                for account in session.scalars(select(MT5Account)).all()
            }
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            trades = self._logical_trade_review_items(session, account_id)
            performance: list[TradePerformanceItem] = []
            for trade in trades:
                trade_account_id = trade.members[0].account_id
                policy = policies_by_id.get(trade.auto_risk_policy_id) or active_policies_by_account.get(trade_account_id)
                funded = funded_capital_by_account.get(trade_account_id)
                effective_risk = None
                if policy is not None and funded is not None:
                    standard_risk = Decimal(funded) * Decimal(policy.standard_risk_per_trade_percent) / Decimal("100")
                    effective_risk = _decimal_string(standard_risk) if standard_risk > 0 else None
                strategy_id = account_strategies.get(trade_account_id)
                strategy = profiles_by_id[strategy_id].name if strategy_id in profiles_by_id else None
                performance.append(
                    TradePerformanceItem(
                        logical_trade_id=trade.id,
                        display_label=trade.display_label,
                        position_ids=trade.position_ids,
                        position_count=trade.position_count,
                        exit_time=trade.exit_time,
                        server_utc_offset_minutes=trade.server_utc_offset_minutes,
                        position_id=trade.position_id,
                        symbol=trade.symbol,
                        direction=trade.direction,
                        net_pnl=trade.net_pnl,
                        result_r=None if effective_risk is None else _decimal_string(Decimal(trade.net_pnl) / Decimal(effective_risk)),
                        strategy=strategy,
                    )
                )
            return sorted(performance, key=lambda item: (item.exit_time, item.logical_trade_id))

    def list_account_balance_movements(self, account_id: int) -> list[AccountBalanceMovement]:
        """Return raw position-close movements for audit/export compatibility."""
        with self._sessions() as session:
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            rows = session.scalars(
                select(Trade)
                .where(Trade.mt5_account_id == account_id)
                .order_by(Trade.exit_time, Trade.id)
            ).all()
            movements: list[AccountBalanceMovement] = []
            for row in rows:
                effective_risk, _ = self._standard_risk_for_trade(
                    row,
                    policies_by_id,
                    active_policies_by_account,
                    funded_capital_by_account,
                )
                movements.append(
                    AccountBalanceMovement(
                        position_id=row.mt5_position_id,
                        exit_time=row.exit_time,
                        server_utc_offset_minutes=row.server_utc_offset_minutes,
                        net_pnl=row.net_pnl,
                        result_r=None if effective_risk is None else _decimal_string(Decimal(row.net_pnl) / Decimal(effective_risk)),
                    )
                )
            return movements

    def upsert_mt5_positions(
        self,
        account_id: int,
        positions: list[MT5PositionExport],
        source_path: str,
        source_hash: str,
        *,
        live_account_balance: Decimal | None = None,
        source_file_mtime_ns: int | None = None,
        source_file_size: int | None = None,
    ) -> ImportResult:
        created = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None or not account.active:
                raise ValueError("Approved MT5 account was not found")
            if live_account_balance is not None:
                account.latest_mt5_balance = _decimal_string(live_account_balance)
            if positions:
                account.latest_server_utc_offset_minutes = positions[0].server_utc_offset_minutes
                unresolved_policies = session.scalars(
                    select(AccountRiskPolicy).where(
                        AccountRiskPolicy.mt5_account_id == account_id,
                        AccountRiskPolicy.server_utc_offset_minutes.is_(None),
                    )
                ).all()
                for unresolved_policy in unresolved_policies:
                    unresolved_policy.server_utc_offset_minutes = positions[0].server_utc_offset_minutes
            active_policy = session.scalar(
                select(AccountRiskPolicy)
                .where(AccountRiskPolicy.mt5_account_id == account_id, AccountRiskPolicy.active.is_(True))
                .order_by(AccountRiskPolicy.version.desc())
            )
            existing_trades_by_position_id = (
                {
                    trade.mt5_position_id: trade
                    for trade in session.scalars(
                        select(Trade).where(
                            Trade.mt5_account_id == account_id,
                            Trade.mt5_position_id.in_([position.position_id for position in positions]),
                        )
                    ).all()
                }
                if positions
                else {}
            )
            for position in positions:
                trade = existing_trades_by_position_id.get(position.position_id)
                values = {
                    "source_updated_at": now,
                    "symbol": position.symbol,
                    "direction": position.direction,
                    "entry_price": _decimal_string(position.entry_price),
                    "exit_price": _decimal_string(position.exit_price),
                    "volume": _decimal_string(position.volume),
                    "gross_pnl": _decimal_string(position.gross_pnl),
                    "commission": _decimal_string(position.commission),
                    "swap": _decimal_string(position.swap),
                    "fees": _decimal_string(position.fees),
                    "net_pnl": _decimal_string(position.net_pnl),
                }
                values.update(
                    {
                        "entry_stop_price": self._optional_decimal_string(position.entry_stop_price),
                        "entry_target_price": self._optional_decimal_string(position.entry_target_price),
                        "close_stop_price": self._optional_decimal_string(position.close_stop_price),
                        "entry_magic_number": self._optional_text(position.entry_magic_number),
                        "entry_deal_count": position.entry_deal_count,
                        "exit_reason": self._optional_text(position.exit_reason),
                        "initial_risk_amount": self._optional_decimal_string(position.initial_risk_amount),
                        "initial_reward_amount": self._optional_decimal_string(position.initial_reward_amount),
                        "pretrade_account_balance": self._optional_decimal_string(position.pretrade_account_balance),
                    }
                )
                if trade is None:
                    imported_times = {
                        "entry_time": normalize_server_timestamp(position.entry_time, position.server_utc_offset_minutes),
                        "exit_time": normalize_server_timestamp(position.exit_time, position.server_utc_offset_minutes),
                        "server_utc_offset_minutes": position.server_utc_offset_minutes,
                    }
                    logical_trade = LogicalTrade(
                        mt5_account_id=account_id,
                        display_label=None,
                        created_at=now,
                    )
                    session.add(logical_trade)
                    session.flush()
                    new_trade = Trade(
                        source="mt5",
                        mt5_account_id=account_id,
                        mt5_position_id=position.position_id,
                        logical_trade_id=logical_trade.id,
                        auto_risk_policy_id=active_policy.id if active_policy else None,
                        **imported_times,
                        **values,
                    )
                    session.add(new_trade)
                    # Guards a duplicate position_id within the same batch: the
                    # pre-fetched map above can't see rows created mid-loop.
                    existing_trades_by_position_id[position.position_id] = new_trade
                    created += 1
                else:
                    for field, value in values.items():
                        setattr(trade, field, value)
                    if trade.auto_risk_policy_id is None and active_policy is not None:
                        trade.auto_risk_policy_id = active_policy.id
                    updated += 1
            session.add(MT5ImportRun(
                mt5_account_id=account_id,
                source_file_path=source_path,
                source_file_hash=source_hash,
                source_file_mtime_ns=source_file_mtime_ns,
                source_file_size=source_file_size,
                status="succeeded",
                created_count=created,
                updated_count=updated,
                skipped_count=0,
                error_count=0,
                created_at=now,
            ))
        return ImportResult(created_count=created, updated_count=updated)

    def get_trade_by_mt5_position(self, login: str, broker_server: str, position_id: str) -> ImportedTradeView | None:
        with self._sessions() as session:
            trade = session.scalar(select(Trade).join(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server, Trade.mt5_position_id == position_id))
            if trade is None:
                return None
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            account_strategies = {account.id: account.strategy_profile_id for account in session.scalars(select(MT5Account)).all()}
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            item = self._to_trade_list_item(
                trade,
                profiles_by_id,
                account_strategies,
                policies_by_id,
                active_policies_by_account,
                funded_capital_by_account,
            )
            return ImportedTradeView(net_pnl=trade.net_pnl, result_r=item.result_r, strategy=item.strategy)

    def count_trades(self) -> int:
        with self._sessions() as session:
            return len(session.scalars(select(Trade)).all())

    @staticmethod
    def _optional_decimal_string(value: Decimal | None) -> str | None:
        return None if value is None else _decimal_string(value)
