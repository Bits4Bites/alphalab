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


MarketLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    AfterValidator(_safe_single_line),
]
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


class MarketOutlookRequest(_StrictModel):
    markets: list[MarketLabel] = Field(default_factory=list, max_length=5)

    @field_validator("markets")
    @classmethod
    def normalize_markets(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            label = " ".join(value.split())
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(label)
        return normalized


class MarketOutlookSource(_StrictModel):
    id: SourceId
    title: SingleLineText
    publisher: SingleLineText
    published_at: datetime.date
    url: SourceUrl


class MarketOutlookEvidence(_StrictModel):
    statement: LongText
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)


class MarketOutlookCatalyst(_StrictModel):
    date: datetime.date
    event: SingleLineText
    expected_impact: ShortText
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)


class MarketOutlookScenario(_StrictModel):
    name: Literal["base", "upside", "downside"]
    description: LongText
    triggers: list[ShortText] = Field(min_length=1, max_length=5)
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)


class MarketOutlookView(_StrictModel):
    market: MarketLabel
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: Literal["high", "medium", "low"]
    outlook: MarketOutlookEvidence
    recent_drivers: list[MarketOutlookEvidence] = Field(min_length=1, max_length=6)
    macro_signals: list[MarketOutlookEvidence] = Field(min_length=1, max_length=6)
    upcoming_catalysts: list[MarketOutlookCatalyst] = Field(max_length=8)
    key_levels: list[MarketOutlookEvidence] = Field(max_length=6)
    scenarios: list[MarketOutlookScenario] = Field(min_length=3, max_length=3)
    relative_strength_themes: list[MarketOutlookEvidence] = Field(min_length=1, max_length=6)
    key_risks: list[MarketOutlookEvidence] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_scenarios(self) -> MarketOutlookView:
        names = [scenario.name for scenario in self.scenarios]
        if set(names) != {"base", "upside", "downside"} or len(set(names)) != 3:
            raise ValueError("scenarios must contain one base, one upside, and one downside case")
        return self


class MarketOutlookReport(_StrictModel):
    as_of: datetime.date
    executive_summary: LongText
    market_outlooks: list[MarketOutlookView] = Field(min_length=1, max_length=5)
    cross_market_risks: list[MarketOutlookEvidence] = Field(max_length=6)
    investor_takeaways: list[MarketOutlookEvidence] = Field(min_length=1, max_length=8)
    sources: list[MarketOutlookSource] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_references(self) -> MarketOutlookReport:
        market_keys = [view.market.casefold() for view in self.market_outlooks]
        if len(market_keys) != len(set(market_keys)):
            raise ValueError("market outlooks must be unique")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        source_urls = [str(source.url) for source in self.sources]
        if len(source_urls) != len(set(source_urls)):
            raise ValueError("source URLs must be unique")

        reference_groups: list[list[str]] = []
        for view in self.market_outlooks:
            reference_groups.append(view.outlook.source_ids)
            reference_groups.extend(item.source_ids for item in view.recent_drivers)
            reference_groups.extend(item.source_ids for item in view.macro_signals)
            reference_groups.extend(item.source_ids for item in view.upcoming_catalysts)
            reference_groups.extend(item.source_ids for item in view.key_levels)
            reference_groups.extend(item.source_ids for item in view.scenarios)
            reference_groups.extend(item.source_ids for item in view.relative_strength_themes)
            reference_groups.extend(item.source_ids for item in view.key_risks)
        reference_groups.extend(item.source_ids for item in self.cross_market_risks)
        reference_groups.extend(item.source_ids for item in self.investor_takeaways)

        references: set[str] = set()
        for group in reference_groups:
            if len(group) != len(set(group)):
                raise ValueError("source references within an item must be unique")
            references.update(group)
        unknown_references = references - set(source_ids)
        if unknown_references:
            raise ValueError("all source references must identify a supplied source")
        return self
