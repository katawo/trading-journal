from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def is_valid_protective_stop(direction: str, stop_price: Decimal, current_price: Decimal) -> bool:
    """A stop only protects a position if it sits on the losing side of the current price."""
    return stop_price < current_price if direction == "long" else stop_price > current_price


class MT5PositionExport(BaseModel):
    """A completed position emitted by the local MQL5 exporter."""

    model_config = ConfigDict(str_strip_whitespace=True)

    schema_version: int = Field(ge=1)
    account_login: str = Field(min_length=1)
    broker_server: str = Field(min_length=1)
    account_currency: str = Field(min_length=3, max_length=3)
    position_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    direction: str = Field(pattern="^(long|short)$")
    entry_time: str = Field(min_length=1)
    exit_time: str = Field(min_length=1)
    server_utc_offset_minutes: int = Field(default=0, ge=-840, le=840)
    entry_price: Decimal
    exit_price: Decimal
    volume: Decimal = Field(gt=0)
    gross_pnl: Decimal
    commission: Decimal
    swap: Decimal
    fees: Decimal
    net_pnl: Decimal
    # Schema v5 evidence. Empty values mean MT5 could not establish that fact;
    # they are deliberately not inferred from the trade outcome.
    entry_stop_price: Decimal | None = None
    entry_target_price: Decimal | None = None
    close_stop_price: Decimal | None = None
    entry_magic_number: str | None = None
    entry_deal_count: int | None = Field(default=None, ge=1)
    exit_reason: str | None = None
    initial_risk_amount: Decimal | None = Field(default=None, gt=0)
    initial_reward_amount: Decimal | None = None
    # Schema v5 snapshot. This is the terminal's current account balance when
    # it exported the CSV, not a historical balance at each trade's close.
    # A depleted account can legitimately report zero or a negative balance.
    # Preserve the snapshot for traceability; automatic Risk scoring ignores it
    # in favour of the separate per-position pre-trade balance below.
    account_balance: Decimal | None = None
    # Calculated in MT5 from its deal ledger as the balance immediately before
    # the position's first entry. Empty means it could not be established.
    pretrade_account_balance: Decimal | None = None

    @field_validator("account_currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator(
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
        mode="before",
    )
    @classmethod
    def blank_optional_evidence_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


class MT5LivePositionExport(BaseModel):
    """One current MT5 position in a replace-on-sync live snapshot."""

    model_config = ConfigDict(str_strip_whitespace=True)

    schema_version: int = Field(ge=1)
    account_login: str = Field(min_length=1)
    broker_server: str = Field(min_length=1)
    account_currency: str = Field(min_length=3, max_length=3)
    snapshot_time: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    direction: str = Field(pattern="^(long|short)$")
    entry_time: str = Field(min_length=1)
    entry_price: Decimal
    current_price: Decimal
    volume: Decimal = Field(gt=0)
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    net_unrealized_pnl: Decimal
    risk_to_stop_amount: Decimal | None = Field(default=None, ge=0)
    magic_number: str | None = None

    @field_validator("account_currency")
    @classmethod
    def uppercase_live_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("stop_price", "target_price", "risk_to_stop_amount", "magic_number", mode="before")
    @classmethod
    def blank_optional_live_evidence_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("snapshot_time", "entry_time")
    @classmethod
    def live_timestamp_is_iso_datetime(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("must be an ISO-8601 timestamp") from error
        return value

    @model_validator(mode="after")
    def risk_requires_a_protective_stop(self) -> "MT5LivePositionExport":
        if self.risk_to_stop_amount is None:
            return self
        if self.stop_price is None:
            raise ValueError("risk_to_stop_amount requires a stop_price")
        if not is_valid_protective_stop(self.direction, self.stop_price, self.current_price):
            raise ValueError("risk_to_stop_amount requires a protective stop")
        return self


class MT5LiveSnapshotExport(BaseModel):
    """Envelope used for empty snapshots as well as remote live ingestion."""

    model_config = ConfigDict(str_strip_whitespace=True)

    schema_version: int = Field(ge=1)
    account_login: str = Field(min_length=1)
    broker_server: str = Field(min_length=1)
    account_currency: str = Field(min_length=3, max_length=3)
    snapshot_time: str = Field(min_length=1)
    export_interval_seconds: int = Field(default=60, ge=1)
    positions: list[MT5LivePositionExport] = Field(default_factory=list)

    @field_validator("account_currency")
    @classmethod
    def uppercase_snapshot_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("snapshot_time")
    @classmethod
    def snapshot_timestamp_is_iso_datetime(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("must be an ISO-8601 timestamp") from error
        return value


class ImportedTradeView(BaseModel):
    """Read model for an imported position and its journal-wide derived values."""

    model_config = ConfigDict(from_attributes=True)

    net_pnl: str
    result_r: str | None
    strategy: str | None


class ImportResult(BaseModel):
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
