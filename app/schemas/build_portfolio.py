from __future__ import annotations

import datetime
import math
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_validator,
)

_UNSAFE_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")


def _safe_text(value: str) -> str:
    if _UNSAFE_CONTROL_PATTERN.search(value):
        raise ValueError("text cannot contain control characters")
    return value


def _safe_single_line(value: str) -> str:
    _safe_text(value)
    if "\r" in value or "\n" in value:
        raise ValueError("text must be a single line")
    return value


SourceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{1,32}$"),
]
SingleLineText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    AfterValidator(_safe_single_line),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=600),
    AfterValidator(_safe_text),
]
LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1800),
    AfterValidator(_safe_text),
]
SourceUrl = Annotated[
    AnyHttpUrl,
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 2083}),
]

RiskTolerance = Literal["Conservative", "Moderate", "Aggressive", "Very Aggressive"]
InvestmentHorizon = Literal[
    "",
    "Short-term (< 1 year)",
    "Medium-term (1-3 years)",
    "Long-term (3-5 years)",
    "Very long-term (5+ years)",
]
BudgetCadence = Literal["total", "weekly", "fortnightly", "monthly", "quarterly", "annual"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class BuildPortfolioRequest(_StrictModel):
    risk_tolerance: RiskTolerance
    portfolio_intent: str = Field(min_length=1, max_length=2500)
    target_market: str = Field(min_length=1, max_length=32)
    investment_horizon: InvestmentHorizon = ""
    budget: str = Field(default="", max_length=80)
    allow_fractional_shares: bool = False
    existing_holdings: str = Field(default="", max_length=6000)

    @field_validator("target_market", "budget")
    @classmethod
    def validate_single_line_text(cls, value: str) -> str:
        return _safe_single_line(value)

    @field_validator("portfolio_intent", "existing_holdings")
    @classmethod
    def validate_free_text(cls, value: str) -> str:
        return _safe_text(value)

    @model_validator(mode="after")
    def validate_aggregate_size(self) -> BuildPortfolioRequest:
        total_size = sum(
            len(value)
            for value in (
                self.portfolio_intent,
                self.target_market,
                self.investment_horizon,
                self.budget,
                self.existing_holdings,
            )
        )
        if total_size > 8500:
            raise ValueError("Build Portfolio request text cannot exceed 8500 characters")
        return self


class PortfolioSource(_StrictModel):
    id: SourceId
    title: SingleLineText
    publisher: SingleLineText
    published_at: datetime.date
    url: SourceUrl


class PortfolioEvidence(_StrictModel):
    statement: ShortText
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)


class ResearchAllocation(_StrictModel):
    ticker: str = Field(min_length=1, max_length=20)
    target_weight_pct: float = Field(gt=0, le=100)
    role: SingleLineText
    rationale: LongText
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper()
        if normalized != "CASH" and not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("ticker must be a bare market symbol or CASH")
        return normalized


class BuildPortfolioResearch(_StrictModel):
    as_of: datetime.date
    market: str = Field(min_length=1, max_length=8)
    strategy_summary: LongText
    allocations: list[ResearchAllocation] = Field(min_length=1, max_length=15)
    portfolio_risks: list[PortfolioEvidence] = Field(min_length=1, max_length=10)
    assumptions: list[ShortText] = Field(max_length=8)
    execution_guidance: list[PortfolioEvidence] = Field(min_length=1, max_length=8)
    tax_considerations: list[PortfolioEvidence] = Field(max_length=8)
    sources: list[PortfolioSource] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_allocations_and_sources(self) -> BuildPortfolioResearch:
        tickers = [allocation.ticker for allocation in self.allocations]
        if len(tickers) != len(set(tickers)):
            raise ValueError("allocation tickers must be unique")
        if not math.isclose(
            math.fsum(allocation.target_weight_pct for allocation in self.allocations),
            100.0,
            rel_tol=0,
            abs_tol=0.05,
        ):
            raise ValueError("target allocation weights must total 100% within 0.05 percentage points")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        source_urls = [str(source.url) for source in self.sources]
        if len(source_urls) != len(set(source_urls)):
            raise ValueError("source URLs must be unique")

        reference_groups = [allocation.source_ids for allocation in self.allocations]
        reference_groups.extend(item.source_ids for item in self.portfolio_risks)
        reference_groups.extend(item.source_ids for item in self.execution_guidance)
        reference_groups.extend(item.source_ids for item in self.tax_considerations)
        references: set[str] = set()
        for group in reference_groups:
            if len(group) != len(set(group)):
                raise ValueError("source references within an item must be unique")
            references.update(group)
        if references - set(source_ids):
            raise ValueError("all source references must identify a supplied source")
        return self


class BudgetSummary(_StrictModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    cadence: BudgetCadence
    label: str = Field(min_length=1, max_length=120)


class VerifiedHolding(_StrictModel):
    ticker: str
    display_name: str
    quantity: float = Field(gt=0)
    current_price: float = Field(gt=0)
    market_value: float = Field(gt=0)
    average_cost: float | None = Field(default=None, ge=0)


class VerifiedAllocation(_StrictModel):
    ticker: str
    display_name: str
    asset_type: str
    exchange: str
    currency: str
    sector: str
    current_price: float | None = Field(default=None, gt=0)
    market_cap: float | None = Field(default=None, gt=0)
    average_volume: int | None = Field(default=None, gt=0)
    target_weight_pct: float = Field(gt=0, le=100)
    target_value: float | None = Field(default=None, ge=0)
    quantity: float | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    role: str
    rationale: str
    source_ids: list[SourceId]


class SectorExposure(_StrictModel):
    sector: str
    target_weight_pct: float = Field(gt=0, le=100)


class PortfolioQuality(_StrictModel):
    largest_position_pct: float = Field(gt=0, le=100)
    largest_sector: str
    largest_sector_pct: float = Field(gt=0, le=100)
    security_count: int = Field(ge=0, le=15)


class BuildPortfolioPayload(_StrictModel):
    generated_at: datetime.datetime
    research_as_of: datetime.date
    market_data_at: datetime.datetime
    market: str
    market_name: str
    currency: str
    risk_tolerance: RiskTolerance
    investment_horizon: str
    portfolio_intent: str
    budget: BudgetSummary | None
    fractional_shares: bool
    strategy_summary: str
    allocations: list[VerifiedAllocation] = Field(min_length=1, max_length=15)
    existing_holdings: list[VerifiedHolding] = Field(max_length=20)
    existing_holdings_value: float | None = Field(default=None, ge=0)
    sector_exposures: list[SectorExposure] = Field(min_length=1, max_length=16)
    quality: PortfolioQuality
    residual_cash: float | None = Field(default=None, ge=0)
    portfolio_risks: list[PortfolioEvidence]
    assumptions: list[str]
    execution_guidance: list[PortfolioEvidence]
    tax_considerations: list[PortfolioEvidence]
    warnings: list[str]
    sources: list[PortfolioSource]
