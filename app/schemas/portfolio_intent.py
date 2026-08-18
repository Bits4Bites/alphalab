from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_QUESTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_UNSAFE_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_PATTERN = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]*>")
_FOLLOW_UP_PATTERN = re.compile(
    r"(?im)^\s*(?:would you|could you|please (?:provide|clarify)|what is|which)\b[^\n]*\?\s*$"
)

PortfolioType = Literal["", "swing_trade", "long_term_growth", "long_term_income", "balanced", "custom"]
RiskTolerance = Literal["", "conservative", "moderate", "aggressive", "very_aggressive", "custom"]
PayoutFrequency = Literal["", "monthly", "quarterly", "semi_annual", "annual", "accumulating"]


def _safe_text(value: str) -> str:
    if _UNSAFE_CONTROL_PATTERN.search(value):
        raise ValueError("text cannot contain control characters")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClarificationQuestion(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    question: str = Field(min_length=1, max_length=300)

    @field_validator("question")
    @classmethod
    def validate_question_text(cls, value: str) -> str:
        return _safe_text(value)


class DraftIntentRequest(_StrictModel):
    market_country: str = Field(default="", max_length=120)
    portfolio_type: PortfolioType = ""
    allocation_split: str = Field(default="", max_length=80)
    budget: str = Field(default="", max_length=80)
    risk_tolerance: RiskTolerance = ""
    holding_horizon: str = Field(default="", max_length=120)
    instrument_preference: str = Field(default="", max_length=160)
    price_preference: str = Field(default="", max_length=120)
    sector_stock_type_focus: str = Field(default="", max_length=300)
    payout_frequency_preference: PayoutFrequency = ""
    excluded_risks_advice_categories: str = Field(default="", max_length=1000)
    market_specific_mechanics: str = Field(default="", max_length=1000)
    additional_context: str = Field(default="", max_length=1500)
    clarification_round: Literal[0, 1] = 0
    prior_questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=3)
    clarifications: dict[str, str] = Field(default_factory=dict, max_length=3)

    @field_validator(
        "market_country",
        "allocation_split",
        "budget",
        "holding_horizon",
        "instrument_preference",
        "price_preference",
        "sector_stock_type_focus",
        "excluded_risks_advice_categories",
        "market_specific_mechanics",
        "additional_context",
    )
    @classmethod
    def validate_free_text(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("clarifications")
    @classmethod
    def validate_clarifications(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_id, raw_answer in value.items():
            question_id = raw_id.strip()
            answer = _safe_text(raw_answer.strip())
            if not _QUESTION_ID_PATTERN.fullmatch(question_id):
                raise ValueError("clarification IDs must use lowercase snake_case")
            if not answer:
                raise ValueError("clarification answers cannot be empty")
            if len(answer) > 600:
                raise ValueError("clarification answers cannot exceed 600 characters")
            normalized[question_id] = answer
        return normalized

    @model_validator(mode="after")
    def validate_round_and_size(self) -> DraftIntentRequest:
        question_ids = [question.id for question in self.prior_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("prior clarification question IDs must be unique")

        if self.clarification_round == 0:
            if self.prior_questions or self.clarifications:
                raise ValueError("round zero cannot include prior clarification state")
        else:
            if not self.prior_questions:
                raise ValueError("round one requires the prior clarification questions")
            if set(self.clarifications) != set(question_ids):
                raise ValueError("round one must answer every prior clarification question exactly once")

        text_size = sum(
            len(value)
            for key, value in self.model_dump(exclude={"prior_questions", "clarifications"}).items()
            if isinstance(value, str)
        )
        text_size += sum(len(question.question) for question in self.prior_questions)
        text_size += sum(len(answer) for answer in self.clarifications.values())
        if text_size > 6500:
            raise ValueError("draft request text cannot exceed 6500 characters")
        return self


class DraftIntentResponse(_StrictModel):
    status: Literal["complete", "needs_clarification"]
    intent: str | None = Field(max_length=2500)
    questions: list[ClarificationQuestion] = Field(max_length=3)
    assumptions: list[str] = Field(max_length=5)

    @field_validator("intent")
    @classmethod
    def validate_intent_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _safe_text(value.replace("\r\n", "\n").replace("\r", "\n").strip())
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", normalized) if paragraph.strip()]
        if not 1 <= len(paragraphs) <= 3:
            raise ValueError("a completed intent must contain one to three paragraphs")
        if _HTML_PATTERN.search(normalized) or "```" in normalized:
            raise ValueError("a completed intent must be plain text")
        if any(line.lstrip().startswith(("#", "* ", "- ")) for line in normalized.splitlines()):
            raise ValueError("a completed intent cannot contain Markdown headings or lists")
        if _FOLLOW_UP_PATTERN.search(normalized):
            raise ValueError("a completed intent cannot include follow-up questions")
        lowered = normalized.casefold()
        if any(phrase in lowered for phrase in ("you are an expert", "write a prompt", "return only the")):
            raise ValueError("a completed intent cannot contain downstream model instructions")
        return "\n\n".join(paragraphs)

    @field_validator("assumptions")
    @classmethod
    def validate_assumptions(cls, value: list[str]) -> list[str]:
        normalized = []
        for assumption in value:
            cleaned = _safe_text(assumption.strip())
            if not cleaned or len(cleaned) > 300:
                raise ValueError("assumptions must contain 1-300 characters")
            normalized.append(cleaned)
        return normalized

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
        if self.assumptions:
            raise ValueError("a clarification response cannot include assumptions")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("clarification question IDs must be unique")
        return self
