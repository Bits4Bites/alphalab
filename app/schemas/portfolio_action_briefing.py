from __future__ import annotations

import datetime
import re
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator

_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
ShortText = Annotated[str, Field(min_length=1, max_length=800)]
SourceUrl = Annotated[AnyHttpUrl, WithJsonSchema({"type": "string", "minLength": 1})]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class BriefingRequest(_StrictModel):
    holdings: str = Field(min_length=1, max_length=6000)
    target_market: str = Field(min_length=1, max_length=32)
    watchlist: str = Field(default="", max_length=500)
    horizon: Literal["today", "7", "14", "30", "90"]
    focus_preset: Literal[
        "",
        "risk_reduction",
        "income_generation",
        "growth_opportunities",
        "tax_loss_harvesting",
        "rebalancing",
        "custom",
    ] = ""
    focus_custom: str = Field(default="", max_length=200)
    risk_tolerance: Literal["", "Conservative", "Moderate", "Aggressive", "Very Aggressive"] = ""
    available_cash: str = Field(default="0", max_length=32)
    additional_context: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_custom_focus(self) -> BriefingRequest:
        if self.focus_preset == "custom" and not self.focus_custom:
            raise ValueError("Custom focus is required when the custom focus option is selected.")
        return self


class ResearchSource(_StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=120)
    url: SourceUrl
    published_at: datetime.date | None


class ResearchAction(_StrictModel):
    ticker: str = Field(min_length=1, max_length=20)
    action: Literal["BUY", "SELL", "TRIM", "HOLD", "WATCH"]
    urgency: Literal["today", "this_week", "this_month", "this_quarter"]
    impact: Literal["high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    rationale: str = Field(min_length=1, max_length=800)
    sizing_pct: float | None = Field(gt=0, le=100)
    source_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper()
        if not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("ticker must be a bare market symbol")
        return normalized

    @model_validator(mode="after")
    def validate_sizing(self) -> ResearchAction:
        if self.action in {"HOLD", "WATCH"} and self.sizing_pct is not None:
            raise ValueError("hold and watch actions cannot include sizing")
        if self.action in {"BUY", "SELL", "TRIM"} and self.sizing_pct is None:
            raise ValueError("buy, sell, and trim actions require sizing_pct")
        return self


class ResearchEvent(_StrictModel):
    date: datetime.date
    ticker: str | None = Field(max_length=20)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=800)
    source_ids: list[str] = Field(min_length=1, max_length=4)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        if not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("event ticker must be a bare market symbol")
        return normalized


class BriefingResearch(_StrictModel):
    as_of: datetime.datetime
    headline: str = Field(min_length=1, max_length=300)
    overall_stance: Literal["Bullish", "Neutral", "Cautious", "Defensive"]
    confidence: Literal["high", "medium", "low"]
    actions: list[ResearchAction] = Field(min_length=1, max_length=20)
    risks: list[ShortText] = Field(min_length=1, max_length=10)
    upcoming_events: list[ResearchEvent] = Field(max_length=20)
    sources: list[ResearchSource] = Field(min_length=1, max_length=15)
    warnings: list[ShortText] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_references(self) -> BriefingResearch:
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        known_sources = set(source_ids)
        references = {source_id for action in self.actions for source_id in action.source_ids}
        references.update(source_id for event in self.upcoming_events for source_id in event.source_ids)
        unknown = references - known_sources
        if unknown:
            raise ValueError(f"unknown source references: {', '.join(sorted(unknown))}")
        return self


class BriefingSummary(_StrictModel):
    headline: str
    portfolio_value: float = Field(ge=0)
    cash_available: float = Field(ge=0)
    priority_actions_count: int = Field(ge=0)
    overall_stance: Literal["Bullish", "Neutral", "Cautious", "Defensive"]
    confidence: Literal["high", "medium", "low"]


class BriefingAction(_StrictModel):
    priority: int = Field(ge=1)
    ticker: str
    action: Literal["BUY", "SELL", "TRIM", "HOLD", "WATCH"]
    urgency: Literal["today", "this_week", "this_month", "this_quarter"]
    rationale: str
    suggested_quantity: float | None = Field(default=None, ge=0)
    estimated_value: float | None = Field(default=None, ge=0)


class BriefingEvent(_StrictModel):
    date: datetime.date
    ticker: str | None
    title: str
    description: str


class BriefingSource(_StrictModel):
    title: str
    publisher: str
    url: SourceUrl
    published_at: datetime.date | None


class BriefingResult(_StrictModel):
    generated_at: datetime.datetime
    market_data_at: datetime.datetime
    market: str
    market_name: str
    currency: str
    horizon: Literal["today", "7", "14", "30", "90"]
    summary: BriefingSummary
    actions: list[BriefingAction] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(min_length=1, max_length=10)
    upcoming_events: list[BriefingEvent] = Field(max_length=20)
    sources: list[BriefingSource] = Field(min_length=1, max_length=15)
    warnings: list[str] = Field(max_length=10)
