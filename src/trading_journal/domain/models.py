from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
