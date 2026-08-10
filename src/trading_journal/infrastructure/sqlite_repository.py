from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import shutil

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from trading_journal.domain.models import ImportResult, ImportedTradeView, MT5PositionExport


_UNSET = object()


def _decimal_string(value: Decimal | str) -> str:
    return str(Decimal(value))


def normalize_strategy_name(value: str) -> str:
    return " ".join(value.split()).casefold()


class Base(DeclarativeBase):
    pass


class JournalSettings(Base):
    __tablename__ = "journal_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reporting_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    monthly_target: Mapped[str] = mapped_column(String, nullable=False)
    default_planned_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
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
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    strategy_profile_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_profiles.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    planned_risk_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    result_r: Mapped[str | None] = mapped_column(String, nullable=True)
    journal_completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


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


@dataclass(frozen=True)
class MT5AccountView:
    id: int
    account_currency: str


@dataclass(frozen=True)
class AccountListItem:
    display_name: str
    login: str
    broker_server: str
    account_currency: str
    export_file_path: str


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
class ImportedTradeAnnotationRef:
    login: str
    broker_server: str
    position_id: str
    symbol: str
    strategy: str | None
    strategy_profile_id: int | None
    planned_risk_amount: str | None
    notes: str | None


@dataclass(frozen=True)
class JournalSettingsView:
    base_currency: str
    reporting_timezone: str
    monthly_target: str
    default_planned_risk_amount: str | None
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
        Base.metadata.create_all(self._engine)
        self._run_additive_migrations()
        self._backfill_strategy_profile_references()

    def _run_additive_migrations(self) -> None:
        migrations = [
            ("journal_settings", "default_planned_risk_amount", "VARCHAR", ".pre-risk-baseline.bak"),
            ("journal_settings", "starting_balance", "VARCHAR", ".pre-balance-analytics.bak"),
            ("journal_settings", "default_strategy_name", "VARCHAR(100)", ".pre-strategy-default.bak"),
            ("journal_settings", "default_strategy_profile_id", "INTEGER", ".pre-strategy-identity.bak"),
            ("trades", "strategy_profile_id", "INTEGER", ".pre-strategy-identity.bak"),
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
            for trade in session.scalars(select(Trade).where(Trade.strategy_profile_id.is_(None), Trade.strategy.is_not(None))).all():
                profile = profiles_by_name.get(normalize_strategy_name(trade.strategy or ""))
                if profile is not None:
                    trade.strategy_profile_id = profile.id
                    trade.strategy = None

    def configure_journal(
        self,
        *,
        base_currency: str,
        reporting_timezone: str,
        monthly_target: str,
        default_planned_risk_amount: str | None | object = _UNSET,
        starting_balance: str | None | object = _UNSET,
    ) -> None:
        if default_planned_risk_amount is not _UNSET and default_planned_risk_amount is not None and Decimal(default_planned_risk_amount) <= 0:
            raise ValueError("Default planned risk must be greater than zero")
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
                        monthly_target=monthly_target,
                        default_planned_risk_amount=None if default_planned_risk_amount is _UNSET else default_planned_risk_amount,
                        starting_balance=None if starting_balance is _UNSET else starting_balance,
                        default_strategy_name=None,
                        default_strategy_profile_id=None,
                    )
                )
            else:
                settings.base_currency = base_currency.upper()
                settings.reporting_timezone = reporting_timezone
                settings.monthly_target = monthly_target
                if default_planned_risk_amount is not _UNSET:
                    settings.default_planned_risk_amount = default_planned_risk_amount
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
                settings.monthly_target,
                settings.default_planned_risk_amount,
                settings.starting_balance,
                default_strategy_name,
                settings.default_strategy_profile_id,
            )

    def journal_base_currency(self) -> str:
        return self.get_journal_settings().base_currency

    def register_mt5_account(self, *, display_name: str, login: str, broker_server: str, account_currency: str, export_file_path: str) -> None:
        with self._sessions.begin() as session:
            existing = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server))
            if existing is None:
                session.add(MT5Account(display_name=display_name, login=login, broker_server=broker_server, account_currency=account_currency.upper(), export_file_path=export_file_path, active=True))
            else:
                existing.display_name = display_name
                existing.account_currency = account_currency.upper()
                existing.export_file_path = export_file_path
                existing.active = True

    def find_active_mt5_account(self, login: str, broker_server: str) -> MT5AccountView | None:
        with self._sessions() as session:
            account = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server, MT5Account.active.is_(True)))
            return None if account is None else MT5AccountView(id=account.id, account_currency=account.account_currency)

    def list_mt5_accounts(self) -> list[AccountListItem]:
        with self._sessions() as session:
            accounts = session.scalars(select(MT5Account).where(MT5Account.active.is_(True)).order_by(MT5Account.display_name)).all()
            return [AccountListItem(account.display_name, account.login, account.broker_server, account.account_currency, account.export_file_path) for account in accounts]

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
            profile.name = clean_name
            profile.description = self._optional_text(description)
            profile.backtest_start_date = None if start_date is None else start_date.isoformat()
            profile.backtest_end_date = None if end_date is None else end_date.isoformat()
            profile.backtest_trade_count = backtest_trade_count
            profile.backtest_win_rate = win_rate
            profile.backtest_expectancy_r = expectancy_r
            profile.backtest_net_r = net_r
            profile.backtest_notes = self._optional_text(backtest_notes)
            session.flush()
            return self._to_strategy_profile_view(profile)

    def get_strategy_profile(self, name: str) -> StrategyProfileView | None:
        with self._sessions() as session:
            profile = session.scalar(select(StrategyProfile).where(StrategyProfile.normalized_name == normalize_strategy_name(name)))
            return None if profile is None else self._to_strategy_profile_view(profile)

    def list_strategy_profiles(self) -> list[StrategyProfileView]:
        with self._sessions() as session:
            profiles = session.scalars(select(StrategyProfile).order_by(StrategyProfile.name)).all()
            return [self._to_strategy_profile_view(profile) for profile in profiles]

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
    def _to_strategy_profile_view(profile: StrategyProfile) -> StrategyProfileView:
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
        )

    def list_trades(self) -> list[TradeListItem]:
        with self._sessions() as session:
            settings = session.get(JournalSettings, 1)
            baseline = None if settings is None else settings.default_planned_risk_amount
            default_strategy_id = None if settings is None else settings.default_strategy_profile_id
            default_strategy_name = None if settings is None else settings.default_strategy_name
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            trades = session.scalars(select(Trade).order_by(Trade.exit_time.desc())).all()
            return [self._to_trade_list_item(trade, baseline, profiles_by_id, default_strategy_id, default_strategy_name) for trade in trades]

    @staticmethod
    def _to_trade_list_item(
        trade: Trade,
        baseline: str | None,
        profiles_by_id: dict[int, StrategyProfile],
        default_strategy_id: int | None,
        default_strategy_name: str | None,
    ) -> TradeListItem:
        effective_risk = trade.planned_risk_amount or baseline
        risk_source = "Override" if trade.planned_risk_amount else "Baseline" if baseline else "Awaiting risk"
        result_r = None if effective_risk is None else _decimal_string(Decimal(trade.net_pnl) / Decimal(effective_risk))
        if trade.strategy_profile_id is not None and trade.strategy_profile_id in profiles_by_id:
            strategy = profiles_by_id[trade.strategy_profile_id].name
            strategy_source = "Override"
        elif trade.strategy:
            strategy = trade.strategy
            strategy_source = "Override"
        elif default_strategy_id is not None and default_strategy_id in profiles_by_id:
            strategy = profiles_by_id[default_strategy_id].name
            strategy_source = "Default"
        else:
            strategy = default_strategy_name
            strategy_source = "Default" if default_strategy_name else "Unassigned"
        return TradeListItem(trade.source, trade.mt5_position_id, trade.symbol, trade.direction, trade.exit_time, trade.net_pnl, result_r, strategy, strategy_source, effective_risk, risk_source)

    def list_imported_trade_annotation_refs(self) -> list[ImportedTradeAnnotationRef]:
        with self._sessions() as session:
            rows = session.execute(
                select(Trade, MT5Account)
                .join(MT5Account, Trade.mt5_account_id == MT5Account.id)
                .where(Trade.source == "mt5")
                .order_by(Trade.exit_time.desc())
            ).all()
            return [
                ImportedTradeAnnotationRef(
                    login=account.login,
                    broker_server=account.broker_server,
                    position_id=trade.mt5_position_id or "",
                    symbol=trade.symbol,
                    strategy=trade.strategy,
                    strategy_profile_id=trade.strategy_profile_id,
                    planned_risk_amount=trade.planned_risk_amount,
                    notes=trade.notes,
                )
                for trade, account in rows
            ]

    def list_trade_performance(self) -> list[TradePerformanceItem]:
        with self._sessions() as session:
            settings = session.get(JournalSettings, 1)
            baseline = None if settings is None else settings.default_planned_risk_amount
            default_strategy_id = None if settings is None else settings.default_strategy_profile_id
            default_strategy_name = None if settings is None else settings.default_strategy_name
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            trades = session.scalars(select(Trade).order_by(Trade.exit_time)).all()
            performance: list[TradePerformanceItem] = []
            for trade in trades:
                item = self._to_trade_list_item(trade, baseline, profiles_by_id, default_strategy_id, default_strategy_name)
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

    def upsert_mt5_positions(self, account_id: int, positions: list[MT5PositionExport], source_path: str, source_hash: str) -> ImportResult:
        created = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._sessions.begin() as session:
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
                if trade is None:
                    session.add(Trade(source="mt5", mt5_account_id=account_id, mt5_position_id=position.position_id, **values))
                    created += 1
                else:
                    for field, value in values.items():
                        setattr(trade, field, value)
                    if trade.planned_risk_amount:
                        trade.result_r = _decimal_string(Decimal(trade.net_pnl) / Decimal(trade.planned_risk_amount))
                    updated += 1
            session.add(MT5ImportRun(mt5_account_id=account_id, source_file_path=source_path, source_file_hash=source_hash, status="succeeded", created_count=created, updated_count=updated, skipped_count=0, error_count=0, created_at=now))
        return ImportResult(created_count=created, updated_count=updated)

    def annotate_imported_trade(
        self,
        *,
        login: str,
        broker_server: str,
        position_id: str,
        strategy: str | None,
        planned_risk_amount: str | None,
        notes: str | None,
        strategy_profile_id: int | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            account = session.scalar(select(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server))
            trade = None if account is None else session.scalar(select(Trade).where(Trade.mt5_account_id == account.id, Trade.mt5_position_id == position_id))
            if trade is None:
                raise KeyError("Imported trade was not found")
            if strategy_profile_id is not None:
                profile = session.get(StrategyProfile, strategy_profile_id)
                if profile is None:
                    raise ValueError("Strategy profile was not found")
                trade.strategy_profile_id = profile.id
                trade.strategy = None
            else:
                trade.strategy_profile_id = None
                trade.strategy = self._optional_text(strategy)
            trade.notes = notes
            trade.planned_risk_amount = planned_risk_amount
            if planned_risk_amount:
                risk = Decimal(planned_risk_amount)
                if risk <= 0:
                    raise ValueError("Planned risk must be greater than zero")
                trade.result_r = _decimal_string(Decimal(trade.net_pnl) / risk)
                trade.journal_completed_at = datetime.now(timezone.utc).isoformat()
            else:
                trade.result_r = None
                trade.journal_completed_at = None

    def get_trade_by_mt5_position(self, login: str, broker_server: str, position_id: str) -> ImportedTradeView | None:
        with self._sessions() as session:
            trade = session.scalar(select(Trade).join(MT5Account).where(MT5Account.login == login, MT5Account.broker_server == broker_server, Trade.mt5_position_id == position_id))
            if trade is None:
                return None
            settings = session.get(JournalSettings, 1)
            baseline = None if settings is None else settings.default_planned_risk_amount
            default_strategy_id = None if settings is None else settings.default_strategy_profile_id
            default_strategy_name = None if settings is None else settings.default_strategy_name
            profiles_by_id = {profile.id: profile for profile in session.scalars(select(StrategyProfile)).all()}
            item = self._to_trade_list_item(trade, baseline, profiles_by_id, default_strategy_id, default_strategy_name)
            return ImportedTradeView(net_pnl=trade.net_pnl, result_r=item.result_r, strategy=item.strategy, notes=trade.notes, is_journal_complete=item.effective_risk is not None)

    def count_trades(self) -> int:
        with self._sessions() as session:
            return len(session.scalars(select(Trade)).all())
