from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_QUESTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftIntentRequest(_StrictModel):
    market_country: str = Field(default="", max_length=120)
    portfolio_type: str = Field(default="", max_length=120)
    allocation_split: str = Field(default="", max_length=80)
    budget: str = Field(default="", max_length=80)
    risk_tolerance: str = Field(default="", max_length=80)
    holding_horizon: str = Field(default="", max_length=120)
    instrument_preference: str = Field(default="", max_length=160)
    price_preference: str = Field(default="", max_length=120)
    sector_stock_type_focus: str = Field(default="", max_length=300)
    payout_frequency_preference: str = Field(default="", max_length=160)
    excluded_risks_advice_categories: str = Field(default="", max_length=1000)
    market_specific_mechanics: str = Field(default="", max_length=1000)
    additional_context: str = Field(default="", max_length=1500)
    clarifications: dict[str, str] = Field(default_factory=dict, max_length=3)

    @field_validator("clarifications")
    @classmethod
    def validate_clarifications(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_id, raw_answer in value.items():
            question_id = raw_id.strip()
            answer = raw_answer.strip()
            if not _QUESTION_ID_PATTERN.fullmatch(question_id):
                raise ValueError("clarification IDs must use lowercase snake_case")
            if not answer:
                raise ValueError("clarification answers cannot be empty")
            if len(answer) > 600:
                raise ValueError("clarification answers cannot exceed 600 characters")
            normalized[question_id] = answer
        return normalized


class ClarificationQuestion(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    question: str = Field(min_length=1, max_length=300)


class DraftIntentResponse(_StrictModel):
    status: Literal["complete", "needs_clarification"]
    intent: str | None = Field(max_length=2500)
    questions: list[ClarificationQuestion] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_status_payload(self) -> DraftIntentResponse:
        if self.status == "complete":
            if not self.intent:
                raise ValueError("a completed response requires an intent")
            if self.questions:
                raise ValueError("a completed response cannot include clarification questions")
            return self

        if self.intent is not None:
            raise ValueError("a clarification response cannot include an intent")
        if not self.questions:
            raise ValueError("a clarification response requires at least one question")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("clarification question IDs must be unique")
        return self
