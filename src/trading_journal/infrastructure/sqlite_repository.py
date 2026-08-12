from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, delete, event, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from trading_journal.application.reporting_time import REPORTING_TIME_BASES, normalize_server_timestamp
from trading_journal.domain.models import ImportResult, ImportedTradeView, MT5PositionExport


_UNSET = object()
ASSESSMENT_GRADES = frozenset({"pass", "partial", "fail"})
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
VIOLATION_CODES = frozenset(
    {
        "fomo_or_chase",
        "revenge",
        "emotional_sizing",
        "post_loss_reset",
        "daily_limit",
        "weekly_limit",
        "drawdown_limit",
        "open_exposure",
        "correlation_exposure",
        "stop_widened",
        "mandatory_setup_absent",
        "shutdown_breach",
    }
)
HARD_RULE_CODES = frozenset(
    {
        "oversized_revenge",
        "mandatory_setup_absent",
        "stop_widened",
        "shutdown_breach",
    }
)


def _decimal_string(value: Decimal | str) -> str:
    return str(Decimal(value))


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
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StrategyProfile(Base):
    __tablename__ = "strategy_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    backtest_start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    backtest_end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    backtest_trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backtest_win_rate: Mapped[str | None] = mapped_column(String, nullable=True)
    backtest_expectancy_r: Mapped[str | None] = mapped_column(String, nullable=True)
    backtest_net_r: Mapped[str | None] = mapped_column(String, nullable=True)
    backtest_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class StrategyMagicNumber(Base):
    __tablename__ = "strategy_magic_numbers"
    __table_args__ = (UniqueConstraint("magic_number", name="uq_strategy_magic_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=False)
    magic_number: Mapped[str] = mapped_column(String(32), nullable=False)


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
    max_open_risk_r: Mapped[str] = mapped_column(String, nullable=False)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_rr: Mapped[str] = mapped_column(String, nullable=False)
    correlation_policy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pretrade_balance_auto_evidence_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class PostTradeAssessment(Base):
    """One complete, post-trade assessment of a logical trade."""

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
    risk_policy_id: Mapped[int | None] = mapped_column(ForeignKey("account_risk_policies.id"), nullable=True)
    strategy_profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=False)
    strategy_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    criterion_grades: Mapped[str] = mapped_column(Text, nullable=False)
    violation_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    hard_rule_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    declared_actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    post_review_note: Mapped[str] = mapped_column(Text, nullable=False)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_position_ids: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_trade_label: Mapped[str] = mapped_column(String(160), nullable=False)
    superseded_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PostTradeAssessmentRevision(Base):
    """Immutable copy of a completed review before it is corrected."""

    __tablename__ = "post_trade_assessment_revisions"
    __table_args__ = (UniqueConstraint("post_trade_assessment_id", "version", name="uq_post_trade_assessment_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_trade_assessment_id: Mapped[int] = mapped_column(ForeignKey("post_trade_assessments.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    criterion_grades: Mapped[str] = mapped_column(Text, nullable=False)
    violation_codes: Mapped[str] = mapped_column(Text, nullable=False)
    hard_rule_codes: Mapped[str] = mapped_column(Text, nullable=False)
    declared_actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    post_review_note: Mapped[str] = mapped_column(Text, nullable=False)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[str] = mapped_column(String(64), nullable=False)


class AutoReviewApproval(Base):
    """A lightweight human approval of immutable automatic risk evidence."""

    __tablename__ = "auto_review_approvals"
    __table_args__ = (
        Index("uq_active_auto_review_approval_logical_trade", "logical_trade_id", unique=True, sqlite_where=text("superseded_at IS NULL")),
        Index("ix_active_auto_review_approvals_account", "mt5_account_id", sqlite_where=text("superseded_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    logical_trade_id: Mapped[int] = mapped_column(ForeignKey("logical_trades.id"), nullable=False)
    risk_policy_id: Mapped[int | None] = mapped_column(ForeignKey("account_risk_policies.id"), nullable=True)
    risk_evidence_source: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    criterion_grades: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


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
    backtest_start_date: str | None
    backtest_end_date: str | None
    backtest_trade_count: int | None
    backtest_win_rate: str | None
    backtest_expectancy_r: str | None
    backtest_net_r: str | None
    backtest_notes: str | None
    magic_numbers: tuple[str, ...]

    @property
    def backtest_period(self) -> str | None:
        if self.backtest_start_date is None:
            return None
        return f"{self.backtest_start_date} to {self.backtest_end_date}"


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
    max_open_risk_r: str
    max_consecutive_losses: int
    minimum_rr: str
    correlation_policy: str | None
    pretrade_balance_auto_evidence_enabled: bool
    created_at: str


@dataclass(frozen=True)
class StrategyEvidenceSnapshot:
    profile_id: int
    name: str
    description: str | None
    backtest_start_date: str | None
    backtest_end_date: str | None
    backtest_trade_count: int | None
    backtest_win_rate: str | None
    backtest_expectancy_r: str | None
    backtest_net_r: str | None
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
    risk_policy_id: int | None
    strategy_profile_id: int
    strategy_snapshot: "StrategyEvidenceSnapshot"
    criterion_grades: dict[str, str]
    violation_codes: tuple[str, ...]
    hard_rule_codes: tuple[str, ...]
    declared_actual_risk_amount: str | None
    post_review_note: str
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
    risk_policy_id: int | None
    strategy_profile_id: int
    strategy_snapshot: "StrategyEvidenceSnapshot"
    criterion_grades: dict[str, str]
    violation_codes: tuple[str, ...]
    hard_rule_codes: tuple[str, ...]
    declared_actual_risk_amount: str | None
    post_review_note: str
    corrective_action: str | None
    archived_at: str


@dataclass(frozen=True)
class PostTradeAssessmentOutcome:
    assessment: PostTradeAssessmentView
    trade: ClosedTradeReviewItem


@dataclass(frozen=True)
class AutoReviewApprovalView:
    id: int
    account_id: int
    trade_id: int
    risk_policy_id: int | None
    risk_evidence_source: str
    risk_policy_state: str
    actual_risk_amount: str | None
    criterion_grades: dict[str, str]
    superseded_at: str | None
    superseded_reason: str | None
    created_at: str


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
            import_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(mt5_import_runs)")}
            if "source_file_mtime_ns" not in import_columns:
                connection.exec_driver_sql("ALTER TABLE mt5_import_runs ADD COLUMN source_file_mtime_ns INTEGER")
            if "source_file_size" not in import_columns:
                connection.exec_driver_sql("ALTER TABLE mt5_import_runs ADD COLUMN source_file_size INTEGER")
            # create_all does not add newly-declared indexes to an existing table.
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_trades_account_exit ON trades (mt5_account_id, exit_time, id)",
                "CREATE INDEX IF NOT EXISTS ix_trades_logical_trade ON trades (logical_trade_id)",
                "CREATE INDEX IF NOT EXISTS ix_import_runs_account_path_status_id ON mt5_import_runs (mt5_account_id, source_file_path, status, id)",
                "CREATE INDEX IF NOT EXISTS ix_active_assessments_account ON post_trade_assessments (mt5_account_id) WHERE superseded_at IS NULL",
                "CREATE INDEX IF NOT EXISTS ix_active_auto_review_approvals_account ON auto_review_approvals (mt5_account_id) WHERE superseded_at IS NULL",
            ):
                connection.exec_driver_sql(statement)
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
                        description="Default strategy for this journal. Document specific setup rules when needed.",
                    )
                    session.add(default)
                    session.flush()
                settings.default_strategy_profile_id = default.id
                settings.default_strategy_name = default.name

    def _require_clean_framework_schema(self) -> None:
        """Greenfield-only persistence: an old database must be reset, never migrated."""
        if not self._database_path.exists():
            return
        expected_columns = {
            "journal_settings": {"reporting_time_basis"},
            "mt5_accounts": {"latest_server_utc_offset_minutes"},
            "trades": {"server_utc_offset_minutes", "logical_trade_id", "pretrade_account_balance"},
            "account_risk_policies": {"pretrade_balance_auto_evidence_enabled"},
            "logical_trades": {"mt5_account_id", "created_at"},
            "post_trade_assessments": {
                "logical_trade_id",
                "criterion_grades",
                "violation_codes",
                "hard_rule_codes",
                "assessed_position_ids",
                "assessed_trade_label",
                "superseded_at",
                "superseded_reason",
            },
            "post_trade_assessment_revisions": {"criterion_grades", "violation_codes", "hard_rule_codes"},
            "auto_review_approvals": {"logical_trade_id", "criterion_grades", "risk_policy_state", "superseded_at"},
        }
        with self._engine.connect() as connection:
            tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            existing_journal_tables = {"journal_settings", "mt5_accounts", "trades", "account_risk_policies", "post_trade_assessments"}
            if tables.intersection(existing_journal_tables) and (
                "framework_rule_settings" not in tables or "auto_review_approvals" not in tables
            ):
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
    ) -> None:
        baseline = None if opening_balance is None or not opening_balance.strip() else _decimal_string(
            self._required_decimal(opening_balance, "Funded capital", minimum=Decimal("0.01"))
        )
        with self._sessions.begin() as session:
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
                        active=True,
                    )
                )
            else:
                if existing.broker_server != broker_server:
                    raise ValueError("This MT5 account ID is already registered with a different broker server")
                existing.display_name = display_name
                existing.account_currency = account_currency.upper()
                existing.export_file_path = export_file_path
                if baseline is not None:
                    existing.opening_balance = baseline
                existing.active = True

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

    def delete_mt5_account(self, account_id: int) -> None:
        """Permanently delete an unimported account and its account-only setup."""
        with self._sessions.begin() as session:
            account = session.get(MT5Account, account_id)
            if account is None:
                raise ValueError("The selected MT5 account no longer exists")
            if session.scalar(select(Trade.id).where(Trade.mt5_account_id == account_id).limit(1)) is not None:
                raise ValueError("An account with imported trades cannot be deleted. Deactivate it to retain its history instead")
            session.execute(delete(MT5ImportRun).where(MT5ImportRun.mt5_account_id == account_id))
            session.execute(delete(AccountRiskPolicy).where(AccountRiskPolicy.mt5_account_id == account_id))
            session.delete(account)

    def find_active_mt5_account(self, login: str, broker_server: str) -> MT5AccountView | None:
        with self._sessions() as session:
            account = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server, MT5Account.active.is_(True)))
            return None if account is None else MT5AccountView(id=account.id, account_currency=account.account_currency)

    def list_mt5_accounts(self) -> list[AccountListItem]:
        with self._sessions() as session:
            accounts = session.scalars(select(MT5Account).where(MT5Account.active.is_(True)).order_by(MT5Account.display_name)).all()
            return [
                AccountListItem(
                    account.id,
                    account.display_name,
                    account.login,
                    account.broker_server,
                    account.account_currency,
                    account.export_file_path,
                    account.opening_balance,
                    account.latest_mt5_balance,
                    account.latest_server_utc_offset_minutes,
                )
                for account in accounts
            ]

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
    ) -> AccountRiskPolicyView:
        standard_risk_percent = self._required_decimal(
            standard_risk_per_trade_percent,
            "Standard risk (1R)",
            minimum=Decimal("0.01"),
            maximum=Decimal("100"),
        )
        maximum_risk_percent = self._required_decimal(
            maximum_risk_per_trade_percent,
            "Maximum risk per trade",
            minimum=Decimal("0.01"),
            maximum=Decimal("100"),
        )
        if maximum_risk_percent < standard_risk_percent:
            raise ValueError("Maximum risk per trade must be at least the standard risk (1R)")
        daily_limit = self._required_decimal(daily_loss_limit_r, "Daily loss limit", minimum=Decimal("0.01"))
        weekly_limit = self._required_decimal(weekly_loss_limit_r, "Weekly loss limit", minimum=Decimal("0.01"))
        max_drawdown = self._required_decimal(max_drawdown_percent, "Maximum drawdown", minimum=Decimal("0.01"), maximum=Decimal("100"))
        open_risk = self._required_decimal(max_open_risk_r, "Maximum open risk", minimum=Decimal("0.01"))
        minimum_rr_value = self._required_decimal(minimum_rr, "Minimum R:R", minimum=Decimal("0.01"))
        if max_consecutive_losses < 1:
            raise ValueError("Maximum consecutive losses must be at least one")
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
                risk_per_trade_percent=_decimal_string(standard_risk_percent),
                maximum_risk_per_trade_percent=_decimal_string(maximum_risk_percent),
                daily_loss_limit_r=_decimal_string(daily_limit),
                weekly_loss_limit_r=_decimal_string(weekly_limit),
                max_drawdown_percent=_decimal_string(max_drawdown),
                max_open_risk_r=_decimal_string(open_risk),
                max_consecutive_losses=max_consecutive_losses,
                minimum_rr=_decimal_string(minimum_rr_value),
                correlation_policy=self._optional_text(correlation_policy),
                pretrade_balance_auto_evidence_enabled=pretrade_balance_auto_evidence_enabled,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(policy)
            session.flush()
            return self._to_risk_policy_view(policy)

    def list_closed_trades_for_review(self, account_id: int) -> list[ClosedTradeReviewItem]:
        """Return logical trades assembled from immutable imported MT5 positions."""
        with self._sessions() as session:
            return self._logical_trade_review_items(session, account_id)

    def list_imported_positions_for_risk(self, account_id: int) -> list[ImportedPositionReviewItem]:
        """Raw chronology for account-level Risk limits; grouping never changes it."""
        with self._sessions() as session:
            rows = session.scalars(
                select(Trade).where(Trade.mt5_account_id == account_id).order_by(Trade.exit_time, Trade.id)
            ).all()
            return [self._to_imported_position_review_item(row) for row in rows]

    def list_groupable_logical_trades(self, account_id: int) -> list[ClosedTradeReviewItem]:
        """Backward-compatible list of singleton logical trades.

        The regrouping UI works from raw positions and may now regroup reviewed
        trades.  This remains useful to callers that only need the default
        one-position units.
        """
        with self._sessions() as session:
            return [
                item
                for item in self._logical_trade_review_items(session, account_id)
                if not item.is_group
            ]

    def list_imported_positions_for_grouping(self, account_id: int) -> list[ImportedPositionReviewItem]:
        """Return every raw MT5 position that may be moved between logical trades."""
        return self.list_imported_positions_for_risk(account_id)

    def preview_logical_trade_regroup(
        self,
        *,
        account_id: int,
        position_trade_ids: tuple[int, ...],
        logical_trade_id: int | None,
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
    ) -> int:
        assessments = self._active_assessments_for_logical_trades(session, logical_trade_ids)
        for assessment in assessments:
            assessment.superseded_at = superseded_at
            assessment.superseded_reason = reason
        approvals = session.scalars(select(AutoReviewApproval).where(
            AutoReviewApproval.logical_trade_id.in_(logical_trade_ids),
            AutoReviewApproval.superseded_at.is_(None),
        )).all() if logical_trade_ids else []
        for approval in approvals:
            approval.superseded_at = superseded_at
            approval.superseded_reason = reason
        return len(assessments) + len(approvals)

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
            if session.scalar(
                select(AutoReviewApproval.id).where(AutoReviewApproval.logical_trade_id == logical_trade_id).limit(1)
            ) is not None:
                continue
            logical_trade = session.get(LogicalTrade, logical_trade_id)
            if logical_trade is not None:
                session.delete(logical_trade)

    def get_post_trade_assessment_for_trade(self, trade_id: int) -> PostTradeAssessmentView | None:
        with self._sessions() as session:
            row = session.scalar(
                select(PostTradeAssessment).where(
                    PostTradeAssessment.logical_trade_id == trade_id,
                    PostTradeAssessment.superseded_at.is_(None),
                )
            )
            return None if row is None else self._to_post_trade_assessment_view(row)

    def list_active_auto_review_approvals(self, account_id: int) -> list[AutoReviewApprovalView]:
        with self._sessions() as session:
            rows = session.scalars(select(AutoReviewApproval).where(
                AutoReviewApproval.mt5_account_id == account_id,
                AutoReviewApproval.superseded_at.is_(None),
            )).all()
            return [self._to_auto_review_approval_view(row) for row in rows]

    def approve_auto_review(self, *, account_id: int, trade_id: int, risk_policy_id: int | None,
                            risk_evidence_source: str, risk_policy_state: str,
                            actual_risk_amount: str | None, criterion_grades: Mapping[str, str]) -> AutoReviewApprovalView:
        grades = self._normalize_criterion_grades(criterion_grades)
        if risk_policy_state not in {"over_policy", "unavailable"}:
            raise ValueError("Only approval-needed automatic risk evidence can be approved")
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            trade = session.get(LogicalTrade, trade_id)
            if trade is None or trade.mt5_account_id != account_id:
                raise ValueError("Logical trade was not found for this account")
            if session.scalar(select(PostTradeAssessment.id).where(PostTradeAssessment.logical_trade_id == trade_id, PostTradeAssessment.superseded_at.is_(None))) is not None:
                raise ValueError("This trade already has a full assessment")
            row = session.scalar(select(AutoReviewApproval).where(AutoReviewApproval.logical_trade_id == trade_id, AutoReviewApproval.superseded_at.is_(None)))
            if row is None:
                row = AutoReviewApproval(mt5_account_id=account_id, logical_trade_id=trade_id, risk_policy_id=risk_policy_id,
                    risk_evidence_source=risk_evidence_source, risk_policy_state=risk_policy_state,
                    actual_risk_amount=actual_risk_amount, criterion_grades=json.dumps(grades, sort_keys=True),
                    superseded_at=None, superseded_reason=None, created_at=now)
                session.add(row)
                session.flush()
            return self._to_auto_review_approval_view(row)

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
        with self._sessions() as session:
            statement = select(PostTradeAssessment).where(PostTradeAssessment.superseded_at.is_(None)).order_by(PostTradeAssessment.updated_at)
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
            raise ValueError("Add at least one reason tag when a criterion fails")
        if (any(grade != "pass" for grade in normalized_grades.values()) or normalized_hard_rules) and not self._optional_text(corrective_action):
            raise ValueError("A corrective action is required for a partial, failed, or hard-rule review")
        actual_risk = None if declared_actual_risk_amount is None or not declared_actual_risk_amount.strip() else _decimal_string(
            self._required_decimal(declared_actual_risk_amount, "Actual risk", minimum=Decimal("0.00000001"))
        )
        review_note = self._required_text(post_review_note, "Post-trade review")
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            trade = session.get(LogicalTrade, trade_id)
            strategy = session.get(StrategyProfile, strategy_profile_id)
            policy = None if risk_policy_id is None else session.get(AccountRiskPolicy, risk_policy_id)
            if trade is None or trade.mt5_account_id != account_id:
                raise ValueError("Logical trade was not found for this account")
            if strategy is None:
                raise ValueError("Strategy profile was not found")
            if policy is not None and policy.mt5_account_id != account_id:
                raise ValueError("Risk policy does not belong to this account")
            for approval in session.scalars(select(AutoReviewApproval).where(
                AutoReviewApproval.logical_trade_id == trade_id,
                AutoReviewApproval.superseded_at.is_(None),
            )).all():
                approval.superseded_at = now
                approval.superseded_reason = "Replaced by full post-trade assessment"
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
                    risk_policy_id=risk_policy_id,
                    strategy_profile_id=strategy_profile_id,
                    strategy_snapshot=self._strategy_snapshot_json(strategy),
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
                session.add(
                    PostTradeAssessmentRevision(
                        post_trade_assessment_id=row.id,
                        version=row.version,
                        risk_policy_id=row.risk_policy_id,
                        strategy_profile_id=row.strategy_profile_id,
                        strategy_snapshot=row.strategy_snapshot,
                        criterion_grades=row.criterion_grades,
                        violation_codes=row.violation_codes,
                        hard_rule_codes=row.hard_rule_codes,
                        declared_actual_risk_amount=row.declared_actual_risk_amount,
                        post_review_note=row.post_review_note,
                        corrective_action=row.corrective_action,
                        archived_at=now,
                    )
                )
                row.risk_policy_id = risk_policy_id
                row.strategy_profile_id = strategy_profile_id
                row.strategy_snapshot = self._strategy_snapshot_json(strategy)
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
            payload = {
                "psychology_score": psychology_score,
                "risk_score": risk_score,
                "system_score": system_score,
                "readiness_score": readiness_score,
                "alert_codes": json.dumps(sorted(set(alert_codes))),
                "recurring_issues": json.dumps(sorted(set(recurring_issues))),
                "review_note": note,
                "priority_action": action,
            }
            if row is None:
                row = FrameworkPeriodReview(
                    mt5_account_id=account_id,
                    cadence=cadence,
                    period_start=period_start,
                    period_end=period_end,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    **payload,
                )
                session.add(row)
            else:
                for field, value in payload.items():
                    setattr(row, field, value)
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
            account_scope = self._roadmap_scope_key("risk", account_id)
            rows = session.scalars(
                select(PillarRoadmapEvidence)
                .where(
                    ((PillarRoadmapEvidence.pillar == "risk") & (PillarRoadmapEvidence.scope_key == account_scope))
                    | ((PillarRoadmapEvidence.pillar != "risk") & (PillarRoadmapEvidence.scope_key == "trader"))
                )
                .order_by(PillarRoadmapEvidence.pillar, PillarRoadmapEvidence.level, PillarRoadmapEvidence.item_key)
            ).all()
            return [PillarRoadmapEvidenceView(row.scope_key, row.pillar, row.level, row.item_key, row.completed, row.evidence_note, row.updated_at) for row in rows]

    def save_pillar_roadmap_evidence(
        self, *, account_id: int | None = None, pillar: str, level: int, item_key: str, completed: bool, evidence_note: str | None
    ) -> PillarRoadmapEvidenceView:
        if pillar not in {"psychology", "risk", "system"} or level not in {1, 2, 3, 4, 5}:
            raise ValueError("Unknown pillar roadmap item")
        if completed and not self._optional_text(evidence_note):
            raise ValueError("An evidence note is required before completing a roadmap item")
        if pillar == "risk" and account_id is None:
            raise ValueError("An account is required for Risk roadmap evidence")
        scope_key = self._roadmap_scope_key(pillar, account_id)
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
    def _roadmap_scope_key(pillar: str, account_id: int | None) -> str:
        return "trader" if pillar in {"psychology", "system"} else f"account:{account_id}"

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

    def save_strategy_profile(
        self,
        *,
        name: str,
        description: str | None,
        backtest_start_date: str | None,
        backtest_end_date: str | None,
        backtest_trade_count: int | None,
        backtest_win_rate: str | None,
        backtest_expectancy_r: str | None,
        backtest_net_r: str | None,
        backtest_notes: str | None,
        magic_numbers: str | None | object = _UNSET,
        strategy_id: int | None = None,
    ) -> StrategyProfileView:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise ValueError("Strategy name is required")
        if len(clean_name) > 100:
            raise ValueError("Strategy name must be 100 characters or fewer")

        start_date = self._validated_optional_date(backtest_start_date, "Backtest start date")
        end_date = self._validated_optional_date(backtest_end_date, "Backtest end date")
        if (start_date is None) != (end_date is None):
            raise ValueError("Backtest start and end dates must be provided together")
        if start_date and end_date and start_date > end_date:
            raise ValueError("Backtest end date must not be before the start date")
        if backtest_trade_count is not None and backtest_trade_count <= 0:
            raise ValueError("Backtest sample size must be greater than zero")

        win_rate = self._validated_optional_decimal(backtest_win_rate, "Backtest win rate", minimum=Decimal("0"), maximum=Decimal("100"))
        expectancy_r = self._validated_optional_decimal(backtest_expectancy_r, "Backtest expectancy R")
        net_r = self._validated_optional_decimal(backtest_net_r, "Backtest net R")
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
            profile.backtest_start_date = None if start_date is None else start_date.isoformat()
            profile.backtest_end_date = None if end_date is None else end_date.isoformat()
            profile.backtest_trade_count = backtest_trade_count
            profile.backtest_win_rate = win_rate
            profile.backtest_expectancy_r = expectancy_r
            profile.backtest_net_r = net_r
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
    def _validated_optional_date(value: str | None, label: str) -> date | None:
        clean_value = SQLiteJournalRepository._optional_text(value)
        if clean_value is None:
            return None
        try:
            return date.fromisoformat(clean_value)
        except ValueError as error:
            raise ValueError(f"{label} must use YYYY-MM-DD") from error

    @staticmethod
    def _validated_optional_decimal(value: str | None, label: str, minimum: Decimal | None = None, maximum: Decimal | None = None) -> str | None:
        clean_value = SQLiteJournalRepository._optional_text(value)
        if clean_value is None:
            return None
        try:
            decimal_value = Decimal(clean_value)
        except ArithmeticError as error:
            raise ValueError(f"{label} must be a number") from error
        if not decimal_value.is_finite():
            raise ValueError(f"{label} must be a finite number")
        if minimum is not None and maximum is not None and not minimum <= decimal_value <= maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}")
        return _decimal_string(decimal_value)

    @staticmethod
    def _to_strategy_profile_view(profile: StrategyProfile, magic_numbers: tuple[str, ...] = ()) -> StrategyProfileView:
        return StrategyProfileView(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            backtest_start_date=profile.backtest_start_date,
            backtest_end_date=profile.backtest_end_date,
            backtest_trade_count=profile.backtest_trade_count,
            backtest_win_rate=profile.backtest_win_rate,
            backtest_expectancy_r=profile.backtest_expectancy_r,
            backtest_net_r=profile.backtest_net_r,
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
            policy.max_open_risk_r,
            policy.max_consecutive_losses,
            policy.minimum_rr,
            policy.correlation_policy,
            policy.pretrade_balance_auto_evidence_enabled,
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
            risk_policy_id=row.risk_policy_id,
            strategy_profile_id=row.strategy_profile_id,
            strategy_snapshot=SQLiteJournalRepository._strategy_snapshot_from_json(row.strategy_snapshot),
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
            risk_policy_id=row.risk_policy_id,
            strategy_profile_id=row.strategy_profile_id,
            strategy_snapshot=SQLiteJournalRepository._strategy_snapshot_from_json(row.strategy_snapshot),
            criterion_grades=dict(json.loads(row.criterion_grades)),
            violation_codes=tuple(json.loads(row.violation_codes)),
            hard_rule_codes=tuple(json.loads(row.hard_rule_codes)),
            declared_actual_risk_amount=row.declared_actual_risk_amount,
            post_review_note=row.post_review_note,
            corrective_action=row.corrective_action,
            archived_at=row.archived_at,
        )

    @staticmethod
    def _to_auto_review_approval_view(row: AutoReviewApproval) -> AutoReviewApprovalView:
        return AutoReviewApprovalView(
            id=row.id, account_id=row.mt5_account_id, trade_id=row.logical_trade_id,
            risk_policy_id=row.risk_policy_id, risk_evidence_source=row.risk_evidence_source,
            risk_policy_state=row.risk_policy_state, actual_risk_amount=row.actual_risk_amount,
            criterion_grades=dict(json.loads(row.criterion_grades)), superseded_at=row.superseded_at,
            superseded_reason=row.superseded_reason, created_at=row.created_at,
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
                "backtest_start_date": profile.backtest_start_date,
                "backtest_end_date": profile.backtest_end_date,
                "backtest_trade_count": profile.backtest_trade_count,
                "backtest_win_rate": profile.backtest_win_rate,
                "backtest_expectancy_r": profile.backtest_expectancy_r,
                "backtest_net_r": profile.backtest_net_r,
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
            backtest_start_date=payload.get("backtest_start_date"),
            backtest_end_date=payload.get("backtest_end_date"),
            backtest_trade_count=payload.get("backtest_trade_count"),
            backtest_win_rate=payload.get("backtest_win_rate"),
            backtest_expectancy_r=payload.get("backtest_expectancy_r"),
            backtest_net_r=payload.get("backtest_net_r"),
            backtest_notes=payload.get("backtest_notes"),
        )

    def list_trades(self) -> list[TradeListItem]:
        with self._sessions() as session:
            settings = session.get(JournalSettings, 1)
            default_strategy_id = None if settings is None else settings.default_strategy_profile_id
            default_strategy_name = None if settings is None else settings.default_strategy_name
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            trades = session.scalars(select(Trade).order_by(Trade.exit_time.desc())).all()
            return [
                self._to_trade_list_item(
                    trade,
                    profiles_by_id,
                    default_strategy_id,
                    default_strategy_name,
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
        default_strategy_id: int | None,
        default_strategy_name: str | None,
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
        if default_strategy_id is not None and default_strategy_id in profiles_by_id:
            strategy = profiles_by_id[default_strategy_id].name
            strategy_source = "Default"
        else:
            strategy = default_strategy_name
            strategy_source = "Default" if default_strategy_name else "Unassigned"
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
            settings = session.get(JournalSettings, 1)
            default_strategy_id = None if settings is None else settings.default_strategy_profile_id
            default_strategy_name = None if settings is None else settings.default_strategy_name
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            assessments = {
                row.logical_trade_id: row
                for row in session.scalars(
                    select(PostTradeAssessment).where(PostTradeAssessment.superseded_at.is_(None))
                ).all()
                if account_id is None or row.mt5_account_id == account_id
            }
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
                assessment = assessments.get(trade.id)
                if assessment is not None:
                    strategy = self._strategy_snapshot_from_json(assessment.strategy_snapshot).name
                elif default_strategy_id is not None and default_strategy_id in profiles_by_id:
                    strategy = profiles_by_id[default_strategy_id].name
                else:
                    strategy = default_strategy_name
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
                        net_pnl=trade.net_pnl,
                        result_r=None if effective_risk is None else _decimal_string(Decimal(trade.net_pnl) / Decimal(effective_risk)),
                        strategy=strategy,
                    )
                )
            return sorted(performance, key=lambda item: (item.exit_time, item.logical_trade_id))

    def list_account_balance_movements(self, account_id: int) -> list[AccountBalanceMovement]:
        """Return raw, immutable position closes for account balance history.

        Logical trades are intentionally mutable review units. They must not
        rewrite the cash-flow chronology used for account balance, daily P&L,
        or drawdown reporting.
        """
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
            active_policy = session.scalar(
                select(AccountRiskPolicy)
                .where(AccountRiskPolicy.mt5_account_id == account_id, AccountRiskPolicy.active.is_(True))
                .order_by(AccountRiskPolicy.version.desc())
            )
            for position in positions:
                trade = session.scalar(select(Trade).where(Trade.mt5_account_id == account_id, Trade.mt5_position_id == position.position_id))
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
                    session.add(
                        Trade(
                            source="mt5",
                            mt5_account_id=account_id,
                            mt5_position_id=position.position_id,
                            logical_trade_id=logical_trade.id,
                            auto_risk_policy_id=active_policy.id if active_policy else None,
                            **imported_times,
                            **values,
                        )
                    )
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
            settings = session.get(JournalSettings, 1)
            default_strategy_id = None if settings is None else settings.default_strategy_profile_id
            default_strategy_name = None if settings is None else settings.default_strategy_name
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            policies_by_id, active_policies_by_account, funded_capital_by_account = self._risk_reporting_context(session)
            item = self._to_trade_list_item(
                trade,
                profiles_by_id,
                default_strategy_id,
                default_strategy_name,
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
