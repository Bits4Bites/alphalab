from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DashboardAnalysisRequest(_StrictModel):
    intent: str = Field(min_length=1, max_length=300)


class DashboardPlan(_StrictModel):
    status: Literal["accepted", "rejected"]
    reason: str | None = Field(max_length=300)
    research_prompt: str | None = Field(max_length=6000)
    disable_web_search: bool

    @model_validator(mode="after")
    def validate_status_payload(self) -> DashboardPlan:
        if self.status == "accepted":
            if self.reason is not None:
                raise ValueError("an accepted plan cannot include a rejection reason")
            if not self.research_prompt:
                raise ValueError("an accepted plan requires a research prompt")
            return self

        if not self.reason:
            raise ValueError("a rejected plan requires a reason")
        if self.research_prompt is not None:
            raise ValueError("a rejected plan cannot include a research prompt")
        if self.disable_web_search:
            raise ValueError("a rejected plan cannot configure analysis tools")
        return self
