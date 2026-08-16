from __future__ import annotations

import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

_UNSAFE_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SINGLE_LINE_FIELDS = {
    "headline",
    "market",
    "publisher",
    "prompt",
    "title",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_unsafe_control_characters(cls, value: object, info) -> object:
        if not isinstance(value, str):
            return value
        if _UNSAFE_CONTROL_PATTERN.search(value):
            raise ValueError("text cannot contain control characters")
        if info.field_name in _SINGLE_LINE_FIELDS and ("\r" in value or "\n" in value):
            raise ValueError(f"{info.field_name} must be a single line")
        return value


class MarketNewsItem(_StrictModel):
    headline: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    market: str = Field(min_length=1, max_length=80)
    published_at: datetime.date
    publisher: str = Field(min_length=1, max_length=120)
    url: HttpUrl


class MarketNewsBatch(_StrictModel):
    as_of: datetime.date
    items: list[MarketNewsItem] = Field(min_length=1, max_length=20)


class IdeaSource(_StrictModel):
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=120)
    published_at: datetime.date
    url: HttpUrl


class ActionableIdea(_StrictModel):
    prompt: str = Field(min_length=1, max_length=300)
    result: str = Field(min_length=1, max_length=2500)
    uncertainty: str = Field(min_length=1, max_length=500)
    sources: list[IdeaSource] = Field(min_length=1, max_length=5)


class ActionableIdeaBatch(_StrictModel):
    as_of: datetime.date
    ideas: list[ActionableIdea] = Field(min_length=1, max_length=10)


class CachedMarketNews(_StrictModel):
    generated_at: float = Field(gt=0)
    news: MarketNewsBatch


class CachedActionableIdeas(_StrictModel):
    generated_at: float = Field(gt=0)
    news_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    ideas: ActionableIdeaBatch
