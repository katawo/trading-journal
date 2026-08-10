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
    entry_price: Decimal
    exit_price: Decimal
    volume: Decimal = Field(gt=0)
    gross_pnl: Decimal
    commission: Decimal
    swap: Decimal
    fees: Decimal
    net_pnl: Decimal

    @field_validator("account_currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class ImportedTradeView(BaseModel):
    """Read model used by the UI and tests."""

    model_config = ConfigDict(from_attributes=True)

    net_pnl: str
    result_r: str | None
    strategy: str | None
    notes: str | None
    is_journal_complete: bool


class ImportResult(BaseModel):
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
