from __future__ import annotations

import datetime
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


def _safe_text(value: str) -> str:
    if _UNSAFE_CONTROL_PATTERN.search(value):
        raise ValueError("text cannot contain control characters")
    return value


def _safe_single_line(value: str) -> str:
    _safe_text(value)
    if "\r" in value or "\n" in value:
        raise ValueError("text must be a single line")
    return value


SingleLineText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    AfterValidator(_safe_single_line),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_safe_text),
]
LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1800),
    AfterValidator(_safe_text),
]
SourceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{1,32}$"),
]
SourceUrl = Annotated[
    AnyHttpUrl,
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 2083}),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_unsafe_control_characters(cls, value: object) -> object:
        if isinstance(value, str):
            _safe_text(value)
        return value


class AnalyzeTickerRequest(_StrictModel):
    ticker: str = Field(min_length=1, max_length=32)
    quick_mode: bool = True
    intent: str = Field(default="", max_length=500)
    scenario: str = Field(default="", max_length=500)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("ticker must be a single line")
        return value.upper()

    @field_validator("intent", "scenario")
    @classmethod
    def require_single_line_context(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("request context must be a single line")
        return value


class TickerAssetSnapshot(_StrictModel):
    requested_ticker: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^(?:[A-Z]{2,10}:)?[A-Z0-9][A-Z0-9.-]{0,19}$",
    )
    yahoo_symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9.-]{0,31}$")
    name: SingleLineText
    asset_type: Literal["stock", "etf", "reit"]
    exchange: SingleLineText
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    country: SingleLineText | None
    sector: SingleLineText | None
    industry: SingleLineText | None
    price: float | None = Field(gt=0)
    market_cap: int | None = Field(ge=0)
    market_cap_tier: SingleLineText
    retrieved_at: datetime.datetime


class TickerResearchSource(_StrictModel):
    id: SourceId
    title: SingleLineText
    publisher: SingleLineText
    published_at: datetime.date
    url: SourceUrl


class TickerEvidence(_StrictModel):
    statement: LongText
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)


class TickerHorizonOutlook(_StrictModel):
    horizon: Literal["2_weeks", "1_month", "3_months"]
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: Literal["high", "medium", "low"]
    thesis: TickerEvidence
    invalidation_conditions: list[ShortText] = Field(min_length=1, max_length=5)


class TickerScenarioAnalysis(_StrictModel):
    base_case: TickerEvidence
    upside_case: TickerEvidence
    downside_case: TickerEvidence
    key_sensitivities: list[TickerEvidence] = Field(min_length=1, max_length=6)


class TickerResearch(_StrictModel):
    as_of: datetime.datetime
    ticker: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9.-]{0,31}$")
    depth: Literal["quick", "full"]
    stance: Literal["bullish", "bearish", "neutral"]
    confidence: Literal["high", "medium", "low"]
    executive_summary: LongText
    business_and_fundamentals: list[TickerEvidence] = Field(min_length=1, max_length=8)
    valuation: list[TickerEvidence] = Field(max_length=8)
    recent_developments: list[TickerEvidence] = Field(min_length=1, max_length=8)
    horizon_outlooks: list[TickerHorizonOutlook] = Field(min_length=3, max_length=3)
    catalysts: list[TickerEvidence] = Field(min_length=1, max_length=8)
    risks: list[TickerEvidence] = Field(min_length=1, max_length=8)
    intent_response: TickerEvidence | None
    scenario_analysis: TickerScenarioAnalysis | None
    sources: list[TickerResearchSource] = Field(min_length=1, max_length=30)
    warnings: list[ShortText] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_horizons_and_references(self) -> TickerResearch:
        horizons = [item.horizon for item in self.horizon_outlooks]
        if set(horizons) != {"2_weeks", "1_month", "3_months"} or len(set(horizons)) != 3:
            raise ValueError("horizon outlooks must contain 2_weeks, 1_month, and 3_months")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        source_urls = [str(source.url) for source in self.sources]
        if len(source_urls) != len(set(source_urls)):
            raise ValueError("source URLs must be unique")

        reference_groups: list[list[str]] = []
        for collection in (
            self.business_and_fundamentals,
            self.valuation,
            self.recent_developments,
            self.catalysts,
            self.risks,
        ):
            reference_groups.extend(item.source_ids for item in collection)
        reference_groups.extend(item.thesis.source_ids for item in self.horizon_outlooks)
        if self.intent_response is not None:
            reference_groups.append(self.intent_response.source_ids)
        if self.scenario_analysis is not None:
            reference_groups.extend(
                (
                    self.scenario_analysis.base_case.source_ids,
                    self.scenario_analysis.upside_case.source_ids,
                    self.scenario_analysis.downside_case.source_ids,
                )
            )
            reference_groups.extend(item.source_ids for item in self.scenario_analysis.key_sensitivities)

        references: set[str] = set()
        for group in reference_groups:
            if len(group) != len(set(group)):
                raise ValueError("source references within an item must be unique")
            references.update(group)
        if references - set(source_ids):
            raise ValueError("all source references must identify a supplied source")
        return self


class AnalyzeTickerPayload(_StrictModel):
    asset: TickerAssetSnapshot
    research: TickerResearch
