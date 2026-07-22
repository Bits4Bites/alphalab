from __future__ import annotations

import datetime
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class TargetAllocation(_StrictModel):
    ticker: str = Field(min_length=1, max_length=20)
    target_weight_pct: float = Field(gt=0, le=100)
    role: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=800)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper()
        if normalized != "CASH" and not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("ticker must be a bare market symbol or CASH")
        return normalized


class TargetAllocationRecommendation(_StrictModel):
    strategy_summary: str = Field(min_length=1, max_length=1500)
    allocations: list[TargetAllocation] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(min_length=1, max_length=10)
    execution_guidance: list[str] = Field(min_length=1, max_length=10)
    tax_considerations: list[str] = Field(max_length=10)

    @field_validator("risks", "execution_guidance", "tax_considerations")
    @classmethod
    def validate_list_items(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("list items must contain between 1 and 500 characters")
        return values

    @model_validator(mode="after")
    def validate_allocations(self) -> TargetAllocationRecommendation:
        tickers = [allocation.ticker for allocation in self.allocations]
        if len(tickers) != len(set(tickers)):
            raise ValueError("allocation tickers must be unique")

        total_weight = math.fsum(allocation.target_weight_pct for allocation in self.allocations)
        if not math.isclose(total_weight, 100.0, rel_tol=0, abs_tol=0.05):
            raise ValueError("target allocation weights must total 100% within 0.05 percentage points")
        return self


class CurrentPosition(_StrictModel):
    ticker: str
    quantity: float = Field(gt=0)
    average_cost: float | None = Field(default=None, ge=0)
    current_price: float = Field(gt=0)
    market_value: float = Field(ge=0)
    current_weight_pct: float = Field(ge=0, le=100)


class ProposedPosition(_StrictModel):
    ticker: str
    target_weight_pct: float = Field(ge=0, le=100)
    resulting_quantity: float | None = Field(default=None, ge=0)
    resulting_value: float = Field(ge=0)
    resulting_weight_pct: float = Field(ge=0, le=100)
    role: str
    rationale: str


class RebalanceTrade(_StrictModel):
    ticker: str
    action: Literal["BUY", "SELL", "TRIM", "HOLD"]
    current_quantity: float = Field(ge=0)
    trade_quantity: float = Field(ge=0)
    resulting_quantity: float = Field(ge=0)
    current_price: float = Field(gt=0)
    estimated_trade_value: float = Field(ge=0)
    current_weight_pct: float = Field(ge=0, le=100)
    target_weight_pct: float = Field(ge=0, le=100)
    resulting_weight_pct: float = Field(ge=0, le=100)


class RebalancePlan(_StrictModel):
    generated_at: datetime.datetime
    market_data_at: datetime.datetime
    market_data_source: str
    market: str
    market_name: str
    currency: str
    tax_context: str
    fractional_shares: bool
    minimum_trade_amount: float = Field(ge=0)
    total_portfolio_value: float = Field(gt=0)
    cash_before: float = Field(ge=0)
    target_cash: float = Field(ge=0)
    cash_after: float = Field(ge=0)
    largest_position_before_pct: float = Field(ge=0, le=100)
    largest_position_after_pct: float = Field(ge=0, le=100)
    strategy_summary: str
    current_positions: list[CurrentPosition]
    proposed_positions: list[ProposedPosition]
    trades: list[RebalanceTrade]
    risks: list[str]
    execution_guidance: list[str]
    tax_considerations: list[str]
    warnings: list[str]


class RebalanceCachePayload(_StrictModel):
    content: str = Field(min_length=1)
    plan: RebalancePlan
