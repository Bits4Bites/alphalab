from __future__ import annotations

import datetime
import decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import analyze_ticker as analyze_ticker_schemas

_UNSAFE_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

HoldingPeriod = Literal["", "short_term", "medium_term", "long_term", "already_holding"]
TaxSituation = Literal["", "tax_free", "low_bracket", "high_bracket", "franking_eligible"]
EventStatus = Literal["confirmed", "unconfirmed", "conflicting", "no_upcoming_event"]
Recommendation = Literal["capture_dividend", "post_dividend_discount", "no_clear_edge"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_unsafe_control_characters(cls, value: object) -> object:
        if isinstance(value, str) and _UNSAFE_CONTROL_PATTERN.search(value):
            raise ValueError("text cannot contain control characters")
        return value


class DividendEventRequest(_StrictModel):
    ticker: str = Field(min_length=1, max_length=32)
    dividend_amount: decimal.Decimal | None = Field(default=None, gt=0, le=1_000_000_000)
    ex_dividend_date: datetime.date | None = None
    current_price: decimal.Decimal | None = Field(default=None, gt=0, le=1_000_000_000_000)
    holding_period: HoldingPeriod = ""
    tax_situation: TaxSituation = ""
    additional_notes: str = Field(default="", max_length=500)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("ticker must be a single line")
        return value.upper()


class DividendHistoryEvent(_StrictModel):
    ex_dividend_date: datetime.date
    dividend_amount: float = Field(gt=0)
    close_before: float | None = Field(gt=0)
    close_on_ex_date: float | None = Field(gt=0)
    price_change: float | None
    price_change_pct: float | None
    adjustment_minus_dividend: float | None
    recovery_trading_days: int | None = Field(ge=0, le=60)


class DividendMarketSnapshot(_StrictModel):
    retrieved_at: datetime.datetime
    provider_current_price: float | None = Field(gt=0)
    user_current_price_hint: float | None = Field(gt=0)
    user_dividend_amount_hint: float | None = Field(gt=0)
    hinted_gross_yield_pct: float | None = Field(ge=0)
    average_price_change: float | None
    average_adjustment_minus_dividend: float | None
    median_recovery_trading_days: float | None = Field(ge=0, le=60)
    history_events: list[DividendHistoryEvent] = Field(max_length=8)
    warnings: list[analyze_ticker_schemas.ShortText] = Field(max_length=8)


class DividendEvidence(_StrictModel):
    statement: analyze_ticker_schemas.LongText
    source_ids: list[analyze_ticker_schemas.SourceId] = Field(min_length=1, max_length=5)


class VerifiedDividendEvent(_StrictModel):
    status: EventStatus
    ex_dividend_date: datetime.date | None
    record_date: datetime.date | None
    payment_date: datetime.date | None
    dividend_amount: float | None = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    indicated_yield_pct: float | None = Field(ge=0, le=100)
    frequency: analyze_ticker_schemas.SingleLineText | None
    evidence: DividendEvidence

    @model_validator(mode="after")
    def require_confirmed_event_fields(self) -> VerifiedDividendEvent:
        if self.status == "confirmed" and (self.ex_dividend_date is None or self.dividend_amount is None):
            raise ValueError("a confirmed event requires an ex-dividend date and dividend amount")
        return self


class DividendStrategyAssessment(_StrictModel):
    summary: analyze_ticker_schemas.LongText
    favorable_conditions: list[analyze_ticker_schemas.ShortText] = Field(min_length=1, max_length=6)
    risks: list[analyze_ticker_schemas.ShortText] = Field(min_length=1, max_length=6)
    source_ids: list[analyze_ticker_schemas.SourceId] = Field(min_length=1, max_length=5)


class DividendResearchSource(_StrictModel):
    id: analyze_ticker_schemas.SourceId
    title: analyze_ticker_schemas.SingleLineText
    publisher: analyze_ticker_schemas.SingleLineText
    published_at: datetime.date
    url: analyze_ticker_schemas.SourceUrl


class DividendEventReport(_StrictModel):
    as_of: datetime.datetime
    ticker: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9.-]{0,31}$")
    recommendation: Recommendation
    confidence: Literal["high", "medium", "low"]
    executive_summary: analyze_ticker_schemas.LongText
    event: VerifiedDividendEvent
    historical_pattern: DividendEvidence
    valuation_and_momentum: DividendEvidence
    capture_strategy: DividendStrategyAssessment
    post_dividend_strategy: DividendStrategyAssessment
    recommendation_rationale: list[DividendEvidence] = Field(min_length=1, max_length=6)
    tax_and_cost_considerations: list[DividendEvidence] = Field(min_length=1, max_length=5)
    key_risks: list[DividendEvidence] = Field(min_length=1, max_length=8)
    invalidation_conditions: list[analyze_ticker_schemas.ShortText] = Field(min_length=1, max_length=8)
    sources: list[DividendResearchSource] = Field(min_length=1, max_length=30)
    warnings: list[analyze_ticker_schemas.ShortText] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_recommendation_and_references(self) -> DividendEventReport:
        if self.event.status != "confirmed" and self.recommendation != "no_clear_edge":
            raise ValueError("an unconfirmed or conflicting event requires a no-clear-edge recommendation")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        source_urls = [str(source.url) for source in self.sources]
        if len(source_urls) != len(set(source_urls)):
            raise ValueError("source URLs must be unique")

        reference_groups = [
            self.event.evidence.source_ids,
            self.historical_pattern.source_ids,
            self.valuation_and_momentum.source_ids,
            self.capture_strategy.source_ids,
            self.post_dividend_strategy.source_ids,
        ]
        for collection in (
            self.recommendation_rationale,
            self.tax_and_cost_considerations,
            self.key_risks,
        ):
            reference_groups.extend(item.source_ids for item in collection)

        references: set[str] = set()
        for group in reference_groups:
            if len(group) != len(set(group)):
                raise ValueError("source references within an item must be unique")
            references.update(group)
        if references - set(source_ids):
            raise ValueError("all source references must identify a supplied source")
        return self


class DividendEventPayload(_StrictModel):
    asset: analyze_ticker_schemas.TickerAssetSnapshot
    market: DividendMarketSnapshot
    report: DividendEventReport
