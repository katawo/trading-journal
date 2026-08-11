from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, delete, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from trading_journal.domain.models import ImportResult, ImportedTradeView, MT5PositionExport


_UNSET = object()
SYSTEM_FAILURE_CODES = frozenset(
    {
        "market_context",
        "session",
        "timeframe",
        "regime",
        "location",
        "confirmation",
        "entry_trigger",
        "invalidation",
        "target",
    }
)


def _decimal_string(value: Decimal | str) -> str:
    return str(Decimal(value))


def normalize_strategy_name(value: str) -> str:
    return " ".join(value.split()).casefold()


class Base(DeclarativeBase):
    pass


class JournalDatabaseResetRequiredError(RuntimeError):
    """Raised when a database predates the removal of monthly targets."""


class JournalSettings(Base):
    __tablename__ = "journal_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reporting_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    starting_balance: Mapped[str | None] = mapped_column(String, nullable=True)
    default_strategy_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_strategy_profile_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=True)


class MT5Account(Base):
    __tablename__ = "mt5_accounts"
    __table_args__ = (UniqueConstraint("login", "broker_server", name="uq_mt5_account_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    login: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_server: Mapped[str] = mapped_column(String(255), nullable=False)
    account_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    opening_balance: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_mt5_balance: Mapped[str | None] = mapped_column(String, nullable=True)
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


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("mt5_account_id", "mt5_position_id", name="uq_mt5_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    mt5_account_id: Mapped[int | None] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=True)
    mt5_position_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[str] = mapped_column(String(64), nullable=False)
    exit_time: Mapped[str] = mapped_column(String(64), nullable=False)
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
    auto_risk_policy_id: Mapped[int | None] = mapped_column(ForeignKey("account_risk_policies.id"), nullable=True)


class MT5ImportRun(Base):
    __tablename__ = "mt5_import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    source_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
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
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class PostTradeAssessment(Base):
    """A review of one immutable, already-imported MT5 closed position."""

    __tablename__ = "post_trade_assessments"
    __table_args__ = (UniqueConstraint("trade_id", name="uq_post_trade_assessment_trade"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mt5_account_id: Mapped[int] = mapped_column(ForeignKey("mt5_accounts.id"), nullable=False)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), nullable=False)
    risk_policy_id: Mapped[int | None] = mapped_column(ForeignKey("account_risk_policies.id"), nullable=True)
    strategy_profile_id: Mapped[int] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=False)
    strategy_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    system_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    system_failure_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    impulse_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revenge_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emotional_size_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stop_widened_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    declared_actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    post_review_note: Mapped[str] = mapped_column(Text, nullable=False)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    system_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    system_failure_codes: Mapped[str] = mapped_column(Text, nullable=False)
    impulse_violation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revenge_violation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    emotional_size_violation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stop_widened_violation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    declared_actual_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    post_review_note: Mapped[str] = mapped_column(Text, nullable=False)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[str] = mapped_column(String(64), nullable=False)


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
    base_currency: str
    reporting_timezone: str
    starting_balance: str | None
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
    exit_time: str
    position_id: str | None
    symbol: str
    net_pnl: str
    result_r: str | None
    strategy: str | None


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
class ClosedTradeReviewItem:
    id: int
    position_id: str | None
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
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


@dataclass(frozen=True)
class PostTradeAssessmentView:
    id: int
    account_id: int
    trade_id: int
    risk_policy_id: int | None
    strategy_profile_id: int
    strategy_snapshot: "StrategyEvidenceSnapshot"
    system_confirmed: bool
    system_failure_codes: tuple[str, ...]
    impulse_violation: bool
    revenge_violation: bool
    emotional_size_violation: bool
    stop_widened_violation: bool
    declared_actual_risk_amount: str | None
    post_review_note: str
    corrective_action: str | None
    created_at: str
    updated_at: str
    version: int


@dataclass(frozen=True)
class PostTradeAssessmentRevisionView:
    version: int
    risk_policy_id: int | None
    strategy_profile_id: int
    strategy_snapshot: "StrategyEvidenceSnapshot"
    system_confirmed: bool
    system_failure_codes: tuple[str, ...]
    impulse_violation: bool
    revenge_violation: bool
    emotional_size_violation: bool
    stop_widened_violation: bool
    declared_actual_risk_amount: str | None
    post_review_note: str
    corrective_action: str | None
    archived_at: str


@dataclass(frozen=True)
class PostTradeAssessmentOutcome:
    assessment: PostTradeAssessmentView
    trade: ClosedTradeReviewItem


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
        self._engine = create_engine(f"sqlite:///{self._database_path}")

        @event.listens_for(self._engine, "connect")
        def configure_sqlite(connection, _record) -> None:  # type: ignore[no-untyped-def]
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")

        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def initialize(self) -> None:
        self._require_reset_for_removed_monthly_target()
        Base.metadata.create_all(self._engine)
        self._remove_trade_overrides()
        self._run_additive_migrations()
        self._backfill_risk_policy_limits()
        self._backfill_strategy_profile_references()

    def _require_reset_for_removed_monthly_target(self) -> None:
        """Do not mutate a legacy database after removing its required settings column."""
        if not self._database_path.exists():
            return
        with self._engine.connect() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(journal_settings)")}
        if "monthly_target" in columns:
            raise JournalDatabaseResetRequiredError(
                "This journal database uses the removed monthly-target schema. "
                "Reset it before starting the app: make reset-db CONFIRM_RESET=yes"
            )

    def _run_additive_migrations(self) -> None:
        migrations = [
            ("journal_settings", "starting_balance", "VARCHAR", ".pre-balance-analytics.bak"),
            ("journal_settings", "default_strategy_name", "VARCHAR(100)", ".pre-strategy-default.bak"),
            ("journal_settings", "default_strategy_profile_id", "INTEGER", ".pre-strategy-identity.bak"),
            ("mt5_accounts", "opening_balance", "VARCHAR", ".pre-account-balance.bak"),
            ("mt5_accounts", "latest_mt5_balance", "VARCHAR", ".pre-live-account-balance.bak"),
            ("post_trade_assessments", "version", "INTEGER NOT NULL DEFAULT 1", ".pre-review-versioning.bak"),
            ("trades", "entry_stop_price", "VARCHAR", ".pre-auto-evidence.bak"),
            ("trades", "entry_target_price", "VARCHAR", ".pre-auto-evidence.bak"),
            ("trades", "close_stop_price", "VARCHAR", ".pre-auto-evidence.bak"),
            ("trades", "entry_magic_number", "VARCHAR(32)", ".pre-auto-evidence.bak"),
            ("trades", "entry_deal_count", "INTEGER", ".pre-auto-evidence.bak"),
            ("trades", "exit_reason", "VARCHAR(32)", ".pre-auto-evidence.bak"),
            ("trades", "initial_risk_amount", "VARCHAR", ".pre-auto-evidence.bak"),
            ("trades", "initial_reward_amount", "VARCHAR", ".pre-auto-evidence.bak"),
            ("trades", "auto_risk_policy_id", "INTEGER", ".pre-auto-evidence.bak"),
            ("account_risk_policies", "maximum_risk_per_trade_percent", "VARCHAR", ".pre-risk-policy-limit.bak"),
        ]
        for table_name, column_name, column_type, backup_suffix in migrations:
            with self._engine.connect() as connection:
                columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})")}
            if column_name in columns:
                continue
            backup_path = self._database_path.with_suffix(self._database_path.suffix + backup_suffix)
            if self._database_path.exists() and not backup_path.exists():
                with self._engine.connect() as connection:
                    connection.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
                shutil.copy2(self._database_path, backup_path)
            with self._engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _backfill_risk_policy_limits(self) -> None:
        """Preserve the prior single-risk behaviour when adding a separate limit."""
        with self._engine.connect() as connection:
            tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "account_risk_policies" not in tables:
                return
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(account_risk_policies)")}
        if "maximum_risk_per_trade_percent" not in columns:
            return
        with self._engine.begin() as connection:
            connection.exec_driver_sql(
                """
                UPDATE account_risk_policies
                SET maximum_risk_per_trade_percent = risk_per_trade_percent
                WHERE maximum_risk_per_trade_percent IS NULL
                   OR TRIM(maximum_risk_per_trade_percent) = ''
                """
            )

    def _remove_trade_overrides(self) -> None:
        removed_columns = {
            "strategy",
            "strategy_profile_id",
            "notes",
            "planned_risk_amount",
            "result_r",
            "journal_completed_at",
        }
        with self._engine.connect() as connection:
            trade_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(trades)")}
        if not trade_columns.intersection(removed_columns):
            return

        backup_path = self._database_path.with_suffix(self._database_path.suffix + ".pre-trade-override-removal.bak")
        if self._database_path.exists() and not backup_path.exists():
            with self._engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
            shutil.copy2(self._database_path, backup_path)

        with self._engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE trades_without_overrides (
                    id INTEGER NOT NULL PRIMARY KEY,
                    source VARCHAR(10) NOT NULL,
                    mt5_account_id INTEGER,
                    mt5_position_id VARCHAR(64),
                    source_updated_at VARCHAR(64) NOT NULL,
                    symbol VARCHAR(32) NOT NULL,
                    direction VARCHAR(8) NOT NULL,
                    entry_time VARCHAR(64) NOT NULL,
                    exit_time VARCHAR(64) NOT NULL,
                    entry_price VARCHAR NOT NULL,
                    exit_price VARCHAR NOT NULL,
                    volume VARCHAR NOT NULL,
                    gross_pnl VARCHAR NOT NULL,
                    commission VARCHAR NOT NULL,
                    swap VARCHAR NOT NULL,
                    fees VARCHAR NOT NULL,
                    net_pnl VARCHAR NOT NULL,
                    entry_stop_price VARCHAR,
                    entry_target_price VARCHAR,
                    close_stop_price VARCHAR,
                    entry_magic_number VARCHAR(32),
                    entry_deal_count INTEGER,
                    exit_reason VARCHAR(32),
                    initial_risk_amount VARCHAR,
                    initial_reward_amount VARCHAR,
                    auto_risk_policy_id INTEGER,
                    CONSTRAINT uq_mt5_position UNIQUE (mt5_account_id, mt5_position_id),
                    FOREIGN KEY(mt5_account_id) REFERENCES mt5_accounts (id)
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO trades_without_overrides (
                    id, source, mt5_account_id, mt5_position_id, source_updated_at,
                    symbol, direction, entry_time, exit_time, entry_price, exit_price,
                    volume, gross_pnl, commission, swap, fees, net_pnl,
                    entry_stop_price, entry_target_price, close_stop_price,
                    entry_magic_number, entry_deal_count, exit_reason,
                    initial_risk_amount, initial_reward_amount, auto_risk_policy_id
                )
                SELECT
                    id, source, mt5_account_id, mt5_position_id, source_updated_at,
                    symbol, direction, entry_time, exit_time, entry_price, exit_price,
                    volume, gross_pnl, commission, swap, fees, net_pnl,
                    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
                FROM trades
                """
            )
            connection.exec_driver_sql("DROP TABLE trades")
            connection.exec_driver_sql("ALTER TABLE trades_without_overrides RENAME TO trades")

    def _backfill_strategy_profile_references(self) -> None:
        with self._sessions.begin() as session:
            profiles = session.scalars(select(StrategyProfile)).all()
            profiles_by_name = {profile.normalized_name: profile for profile in profiles}
            settings = session.get(JournalSettings, 1)
            if settings and settings.default_strategy_profile_id is None and settings.default_strategy_name:
                profile = profiles_by_name.get(normalize_strategy_name(settings.default_strategy_name))
                if profile is not None:
                    settings.default_strategy_profile_id = profile.id
                    settings.default_strategy_name = profile.name

    def configure_journal(
        self,
        *,
        base_currency: str,
        reporting_timezone: str,
        starting_balance: str | None | object = _UNSET,
    ) -> None:
        if starting_balance is not _UNSET and starting_balance is not None and Decimal(starting_balance) <= 0:
            raise ValueError("Starting balance must be greater than zero")
        with self._sessions.begin() as session:
            settings = session.get(JournalSettings, 1)
            if settings is None:
                session.add(
                    JournalSettings(
                        id=1,
                        base_currency=base_currency.upper(),
                        reporting_timezone=reporting_timezone,
                        starting_balance=None if starting_balance is _UNSET else starting_balance,
                        default_strategy_name=None,
                        default_strategy_profile_id=None,
                    )
                )
            else:
                settings.base_currency = base_currency.upper()
                settings.reporting_timezone = reporting_timezone
                if starting_balance is not _UNSET:
                    settings.starting_balance = starting_balance

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
                settings.base_currency,
                settings.reporting_timezone,
                settings.starting_balance,
                default_strategy_name,
                settings.default_strategy_profile_id,
            )

    def journal_base_currency(self) -> str:
        return self.get_journal_settings().base_currency

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
            existing = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server))
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
                    MT5Account.broker_server == broker_server,
                    MT5Account.id != account_id,
                )
            )
            if duplicate is not None:
                raise ValueError("Another account already uses this MT5 account ID and broker server")

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
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(policy)
            session.flush()
            return self._to_risk_policy_view(policy)

    def list_closed_trades_for_review(self, account_id: int) -> list[ClosedTradeReviewItem]:
        """Return immutable MT5 positions that can be assessed after they close."""
        with self._sessions() as session:
            rows = session.scalars(
                select(Trade).where(Trade.mt5_account_id == account_id).order_by(Trade.exit_time.desc(), Trade.id.desc())
            ).all()
            return [self._to_closed_trade_review_item(row) for row in rows]

    def get_post_trade_assessment_for_trade(self, trade_id: int) -> PostTradeAssessmentView | None:
        with self._sessions() as session:
            row = session.scalar(select(PostTradeAssessment).where(PostTradeAssessment.trade_id == trade_id))
            return None if row is None else self._to_post_trade_assessment_view(row)

    def list_post_trade_assessment_revisions(self, trade_id: int) -> list[PostTradeAssessmentRevisionView]:
        """Return the immutable prior versions of a review, newest first."""
        with self._sessions() as session:
            rows = session.scalars(
                select(PostTradeAssessmentRevision)
                .join(PostTradeAssessment)
                .where(PostTradeAssessment.trade_id == trade_id)
                .order_by(PostTradeAssessmentRevision.version.desc())
            ).all()
            return [self._to_post_trade_assessment_revision_view(row) for row in rows]

    def list_post_trade_assessment_outcomes(self, account_id: int | None = None) -> list[PostTradeAssessmentOutcome]:
        with self._sessions() as session:
            statement = select(PostTradeAssessment, Trade).join(Trade, PostTradeAssessment.trade_id == Trade.id).order_by(Trade.exit_time)
            if account_id is not None:
                statement = statement.where(PostTradeAssessment.mt5_account_id == account_id)
            rows = session.execute(statement).all()
            return [
                PostTradeAssessmentOutcome(self._to_post_trade_assessment_view(assessment), self._to_closed_trade_review_item(trade))
                for assessment, trade in rows
            ]

    def save_post_trade_assessment(
        self,
        *,
        account_id: int,
        trade_id: int,
        risk_policy_id: int | None,
        strategy_profile_id: int,
        system_confirmed: bool,
        system_failure_codes: tuple[str, ...],
        impulse_violation: bool,
        revenge_violation: bool,
        emotional_size_violation: bool,
        stop_widened_violation: bool,
        declared_actual_risk_amount: str | None,
        post_review_note: str,
        corrective_action: str | None,
    ) -> PostTradeAssessmentView:
        """Create or correct the review for one already-imported closed position."""
        unknown_failure_codes = set(system_failure_codes) - SYSTEM_FAILURE_CODES
        if unknown_failure_codes:
            raise ValueError("Unknown Trading System failure code")
        if system_confirmed and system_failure_codes:
            raise ValueError("A confirmed Trading System cannot contain failure codes")
        actual_risk = None if declared_actual_risk_amount is None or not declared_actual_risk_amount.strip() else _decimal_string(
            self._required_decimal(declared_actual_risk_amount, "Actual risk", minimum=Decimal("0.00000001"))
        )
        review_note = self._required_text(post_review_note, "Post-trade review")
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
            trade = session.get(Trade, trade_id)
            strategy = session.get(StrategyProfile, strategy_profile_id)
            policy = None if risk_policy_id is None else session.get(AccountRiskPolicy, risk_policy_id)
            if trade is None or trade.mt5_account_id != account_id:
                raise ValueError("Imported closed trade was not found for this account")
            if strategy is None:
                raise ValueError("Strategy profile was not found")
            if policy is not None and policy.mt5_account_id != account_id:
                raise ValueError("Risk policy does not belong to this account")
            row = session.scalar(select(PostTradeAssessment).where(PostTradeAssessment.trade_id == trade_id))
            if row is None:
                row = PostTradeAssessment(
                    mt5_account_id=account_id,
                    trade_id=trade_id,
                    risk_policy_id=risk_policy_id,
                    strategy_profile_id=strategy_profile_id,
                    strategy_snapshot=self._strategy_snapshot_json(strategy),
                    system_confirmed=system_confirmed,
                    system_failure_codes=json.dumps(sorted(set(system_failure_codes))),
                    impulse_violation=impulse_violation,
                    revenge_violation=revenge_violation,
                    emotional_size_violation=emotional_size_violation,
                    stop_widened_violation=stop_widened_violation,
                    declared_actual_risk_amount=actual_risk,
                    post_review_note=review_note,
                    corrective_action=self._optional_text(corrective_action),
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
                        system_confirmed=row.system_confirmed,
                        system_failure_codes=row.system_failure_codes,
                        impulse_violation=row.impulse_violation,
                        revenge_violation=row.revenge_violation,
                        emotional_size_violation=row.emotional_size_violation,
                        stop_widened_violation=row.stop_widened_violation,
                        declared_actual_risk_amount=row.declared_actual_risk_amount,
                        post_review_note=row.post_review_note,
                        corrective_action=row.corrective_action,
                        archived_at=now,
                    )
                )
                row.risk_policy_id = risk_policy_id
                row.strategy_profile_id = strategy_profile_id
                row.strategy_snapshot = self._strategy_snapshot_json(strategy)
                row.system_confirmed = system_confirmed
                row.system_failure_codes = json.dumps(sorted(set(system_failure_codes)))
                row.impulse_violation = impulse_violation
                row.revenge_violation = revenge_violation
                row.emotional_size_violation = emotional_size_violation
                row.stop_widened_violation = stop_widened_violation
                row.declared_actual_risk_amount = actual_risk
                row.post_review_note = review_note
                row.corrective_action = self._optional_text(corrective_action)
                row.updated_at = now
                row.version += 1
            session.flush()
            return self._to_post_trade_assessment_view(row)

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
            policy.created_at,
        )

    @staticmethod
    def _to_closed_trade_review_item(row: Trade) -> ClosedTradeReviewItem:
        return ClosedTradeReviewItem(
            id=row.id,
            position_id=row.mt5_position_id,
            symbol=row.symbol,
            direction=row.direction,
            entry_time=row.entry_time,
            exit_time=row.exit_time,
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
            auto_risk_policy_id=row.auto_risk_policy_id,
        )

    @staticmethod
    def _to_post_trade_assessment_view(row: PostTradeAssessment) -> PostTradeAssessmentView:
        return PostTradeAssessmentView(
            id=row.id,
            account_id=row.mt5_account_id,
            trade_id=row.trade_id,
            risk_policy_id=row.risk_policy_id,
            strategy_profile_id=row.strategy_profile_id,
            strategy_snapshot=SQLiteJournalRepository._strategy_snapshot_from_json(row.strategy_snapshot),
            system_confirmed=row.system_confirmed,
            system_failure_codes=tuple(json.loads(row.system_failure_codes)),
            impulse_violation=row.impulse_violation,
            revenge_violation=row.revenge_violation,
            emotional_size_violation=row.emotional_size_violation,
            stop_widened_violation=row.stop_widened_violation,
            declared_actual_risk_amount=row.declared_actual_risk_amount,
            post_review_note=row.post_review_note,
            corrective_action=row.corrective_action,
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
            system_confirmed=row.system_confirmed,
            system_failure_codes=tuple(json.loads(row.system_failure_codes)),
            impulse_violation=row.impulse_violation,
            revenge_violation=row.revenge_violation,
            emotional_size_violation=row.emotional_size_violation,
            stop_widened_violation=row.stop_widened_violation,
            declared_actual_risk_amount=row.declared_actual_risk_amount,
            post_review_note=row.post_review_note,
            corrective_action=row.corrective_action,
            archived_at=row.archived_at,
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
            statement = select(Trade).order_by(Trade.exit_time)
            if account_id is not None:
                statement = statement.where(Trade.mt5_account_id == account_id)
            trades = session.scalars(statement).all()
            performance: list[TradePerformanceItem] = []
            for trade in trades:
                item = self._to_trade_list_item(
                    trade,
                    profiles_by_id,
                    default_strategy_id,
                    default_strategy_name,
                    policies_by_id,
                    active_policies_by_account,
                    funded_capital_by_account,
                )
                performance.append(
                    TradePerformanceItem(
                        exit_time=trade.exit_time,
                        position_id=trade.mt5_position_id,
                        symbol=trade.symbol,
                        net_pnl=trade.net_pnl,
                        result_r=item.result_r,
                        strategy=item.strategy,
                    )
                )
            return performance

    def upsert_mt5_positions(
        self,
        account_id: int,
        positions: list[MT5PositionExport],
        source_path: str,
        source_hash: str,
        *,
        live_account_balance: Decimal | None = None,
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
                    "entry_time": position.entry_time,
                    "exit_time": position.exit_time,
                    "entry_price": _decimal_string(position.entry_price),
                    "exit_price": _decimal_string(position.exit_price),
                    "volume": _decimal_string(position.volume),
                    "gross_pnl": _decimal_string(position.gross_pnl),
                    "commission": _decimal_string(position.commission),
                    "swap": _decimal_string(position.swap),
                    "fees": _decimal_string(position.fees),
                    "net_pnl": _decimal_string(position.net_pnl),
                }
                if position.schema_version >= 2:
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
                        }
                    )
                if trade is None:
                    session.add(
                        Trade(
                            source="mt5",
                            mt5_account_id=account_id,
                            mt5_position_id=position.position_id,
                            auto_risk_policy_id=active_policy.id if active_policy else None,
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
            session.add(MT5ImportRun(mt5_account_id=account_id, source_file_path=source_path, source_file_hash=source_hash, status="succeeded", created_count=created, updated_count=updated, skipped_count=0, error_count=0, created_at=now))
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
