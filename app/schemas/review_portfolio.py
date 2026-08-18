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

from app.schemas import portfolio_rebalance as portfolio_rebalance_schemas

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
ActionId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^A[1-9][0-9]{0,2}$"),
]
SingleLineText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    AfterValidator(_safe_single_line),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=700),
    AfterValidator(_safe_text),
]
LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    AfterValidator(_safe_text),
]
SourceUrl = Annotated[
    AnyHttpUrl,
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 2083}),
]

RiskTolerance = Literal["", "Conservative", "Moderate", "Aggressive", "Very Aggressive"]
InvestmentHorizon = Literal[
    "",
    "Short-term (< 1 year)",
    "Medium-term (1-3 years)",
    "Long-term (3-5 years)",
    "Very long-term (5+ years)",
]
TaxContext = Literal["unknown", "taxable", "tax-advantaged"]
BudgetCadence = Literal["total", "weekly", "fortnightly", "monthly", "quarterly", "annual"]
RebalanceNeed = Literal["none", "minor", "major"]
ReviewActionType = Literal["NEW", "ADD", "HOLD", "TRIM", "EXIT"]
ReviewActionPriority = Literal["Critical", "High", "Medium", "Low"]
ActionSizingBasis = Literal["target_portfolio", "current_position", "none"]
ActionPlanBasis = Literal["review_only", "rebalance"]
ActionSourceScope = Literal["review", "rebalance"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class ReviewPortfolioRequest(_StrictModel):
    holdings: str = Field(min_length=1, max_length=6000)
    risk_tolerance: RiskTolerance = ""
    investment_goals: str = Field(default="", max_length=2500)
    target_market: str = Field(min_length=1, max_length=32)
    investment_horizon: InvestmentHorizon = ""
    scenario: str = Field(default="", max_length=1000)
    include_rebalance: bool = False
    available_cash: str = Field(default="0", max_length=32)
    additional_budget: str = Field(default="", max_length=80)
    allow_fractional_shares: bool = False
    minimum_trade_amount: str = Field(default="0", max_length=32)
    tax_context: TaxContext = "unknown"

    @field_validator("target_market", "available_cash", "additional_budget", "minimum_trade_amount")
    @classmethod
    def validate_single_line_text(cls, value: str) -> str:
        return _safe_single_line(value)

    @field_validator("holdings", "investment_goals", "scenario")
    @classmethod
    def validate_free_text(cls, value: str) -> str:
        return _safe_text(value)

    @model_validator(mode="after")
    def validate_aggregate_size(self) -> ReviewPortfolioRequest:
        total_size = sum(
            len(value)
            for value in (
                self.holdings,
                self.risk_tolerance,
                self.investment_goals,
                self.target_market,
                self.investment_horizon,
                self.scenario,
                self.available_cash,
                self.additional_budget,
                self.minimum_trade_amount,
                self.tax_context,
            )
        )
        if total_size > 10500:
            raise ValueError("Review Portfolio request text cannot exceed 10500 characters")
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


class PositionAssessment(_StrictModel):
    ticker: str = Field(min_length=1, max_length=20)
    fundamental_status: Literal["healthy", "watch", "impaired", "not_applicable"]
    recommendation: Literal["HOLD", "TRIM", "EXIT"]
    assessment: LongText
    portfolio_fit: ShortText
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper()
        if not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("ticker must be a bare market symbol")
        return normalized


class ScenarioAssessment(_StrictModel):
    scenario: ShortText
    portfolio_impact: LongText
    vulnerable_tickers: list[str] = Field(max_length=20)
    resilient_tickers: list[str] = Field(max_length=20)
    source_ids: list[SourceId] = Field(min_length=1, max_length=8)

    @field_validator("vulnerable_tickers", "resilient_tickers")
    @classmethod
    def validate_ticker_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("scenario ticker lists must be unique")
        if any(not _TICKER_PATTERN.fullmatch(value) for value in normalized):
            raise ValueError("scenario ticker lists must contain bare market symbols")
        return normalized


class RebalanceAssessment(_StrictModel):
    need: RebalanceNeed
    confidence: Literal["low", "medium", "high"]
    urgency: Literal["monitor", "next_contribution", "near_term", "prompt"]
    summary: LongText
    drivers: list[PortfolioEvidence] = Field(min_length=1, max_length=8)


class PortfolioReviewResearch(_StrictModel):
    as_of: datetime.date
    market: str = Field(min_length=1, max_length=8)
    portfolio_summary: LongText
    diversification_findings: list[PortfolioEvidence] = Field(min_length=1, max_length=10)
    position_assessments: list[PositionAssessment] = Field(min_length=1, max_length=20)
    scenario_assessment: ScenarioAssessment | None
    portfolio_risks: list[PortfolioEvidence] = Field(min_length=1, max_length=10)
    urgent_actions: list[PortfolioEvidence] = Field(max_length=8)
    review_triggers: list[PortfolioEvidence] = Field(min_length=1, max_length=8)
    tax_considerations: list[PortfolioEvidence] = Field(max_length=8)
    rebalance_assessment: RebalanceAssessment
    assumptions: list[ShortText] = Field(max_length=8)
    sources: list[PortfolioSource] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_tickers_and_sources(self) -> PortfolioReviewResearch:
        tickers = [position.ticker for position in self.position_assessments]
        if len(tickers) != len(set(tickers)):
            raise ValueError("position assessment tickers must be unique")
        _validate_source_contract(
            self.sources,
            [
                *(item.source_ids for item in self.diversification_findings),
                *(position.source_ids for position in self.position_assessments),
                *(item.source_ids for item in self.portfolio_risks),
                *(item.source_ids for item in self.urgent_actions),
                *(item.source_ids for item in self.review_triggers),
                *(item.source_ids for item in self.tax_considerations),
                *(item.source_ids for item in self.rebalance_assessment.drivers),
                *([self.scenario_assessment.source_ids] if self.scenario_assessment else []),
            ],
        )
        return self


class TargetAllocation(_StrictModel):
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


class RebalanceResearch(_StrictModel):
    as_of: datetime.date
    market: str = Field(min_length=1, max_length=8)
    strategy_summary: LongText
    allocations: list[TargetAllocation] = Field(min_length=1, max_length=20)
    portfolio_risks: list[PortfolioEvidence] = Field(min_length=1, max_length=10)
    execution_guidance: list[PortfolioEvidence] = Field(min_length=1, max_length=10)
    tax_considerations: list[PortfolioEvidence] = Field(max_length=10)
    assumptions: list[ShortText] = Field(max_length=8)
    sources: list[PortfolioSource] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_allocations_and_sources(self) -> RebalanceResearch:
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
        _validate_source_contract(
            self.sources,
            [
                *(allocation.source_ids for allocation in self.allocations),
                *(item.source_ids for item in self.portfolio_risks),
                *(item.source_ids for item in self.execution_guidance),
                *(item.source_ids for item in self.tax_considerations),
            ],
        )
        return self


class ReviewActionCandidate(_StrictModel):
    action_id: ActionId
    ticker: str
    display_name: str
    allowed_actions: list[ReviewActionType] = Field(min_length=1, max_length=4)
    sizing_locked: bool
    current_quantity: float = Field(ge=0)
    current_price: float = Field(gt=0)
    current_market_value: float = Field(ge=0)
    current_weight_pct: float = Field(ge=0, le=100)
    no_trade_weight_pct: float = Field(ge=0, le=100)
    target_weight_pct: float | None = Field(default=None, ge=0, le=100)
    sizing_pct: float | None = Field(default=None, gt=0, le=100)
    estimated_quantity: float | None = Field(default=None, gt=0)
    estimated_value: float | None = Field(default=None, ge=0)
    strategic_rationale: LongText
    allowed_source_ids: list[SourceId] = Field(max_length=5)
    source_scope: ActionSourceScope

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> ReviewActionCandidate:
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("allowed actions must be unique")
        if "NEW" in self.allowed_actions and self.current_quantity > 0:
            raise ValueError("NEW is only valid for a security outside the current portfolio")
        return self


class ReviewActionDecision(_StrictModel):
    action_id: ActionId
    action: ReviewActionType
    priority: ReviewActionPriority
    rationale: LongText
    sizing_pct: float | None = Field(gt=0, le=100)
    dependency_ids: list[ActionId] = Field(max_length=5)
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)


class ReviewActionPlanResearch(_StrictModel):
    summary: LongText
    actions: list[ReviewActionDecision] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_actions(self) -> ReviewActionPlanResearch:
        action_ids = [action.action_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action IDs must be unique")
        known_ids = set(action_ids)
        for action in self.actions:
            if len(action.dependency_ids) != len(set(action.dependency_ids)):
                raise ValueError("action dependencies within an item must be unique")
            if action.action_id in action.dependency_ids:
                raise ValueError("an action cannot depend on itself")
            if set(action.dependency_ids) - known_ids:
                raise ValueError("all action dependencies must identify a supplied action")
            if len(action.source_ids) != len(set(action.source_ids)):
                raise ValueError("action source references within an item must be unique")
        return self


def _validate_source_contract(
    sources: list[PortfolioSource],
    reference_groups: list[list[str]],
) -> None:
    source_ids = [source.id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source IDs must be unique")
    source_urls = [str(source.url) for source in sources]
    if len(source_urls) != len(set(source_urls)):
        raise ValueError("source URLs must be unique")

    references: set[str] = set()
    for group in reference_groups:
        if len(group) != len(set(group)):
            raise ValueError("source references within an item must be unique")
        references.update(group)
    if references - set(source_ids):
        raise ValueError("all source references must identify a supplied source")


class ContributionBudget(_StrictModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    cadence: BudgetCadence
    label: str = Field(min_length=1, max_length=120)


class ReviewedPosition(_StrictModel):
    ticker: str
    display_name: str
    asset_type: str
    exchange: str
    sector: str
    quantity: float = Field(gt=0)
    average_cost: float | None = Field(default=None, ge=0)
    current_price: float = Field(gt=0)
    market_cap: float | None = Field(default=None, gt=0)
    average_volume: int | None = Field(default=None, gt=0)
    market_value: float = Field(gt=0)
    current_weight_pct: float = Field(gt=0, le=100)
    fundamental_status: str
    recommendation: str
    assessment: str
    portfolio_fit: str
    source_ids: list[SourceId]


class SectorExposure(_StrictModel):
    sector: str
    current_weight_pct: float = Field(gt=0, le=100)


class ReviewPortfolioPayload(_StrictModel):
    generated_at: datetime.datetime
    research_as_of: datetime.date
    market_data_at: datetime.datetime
    market: str
    market_name: str
    currency: str
    risk_tolerance: str
    investment_horizon: str
    investment_goals: str
    scenario: str
    tax_context: str
    available_cash: float = Field(ge=0)
    additional_budget: ContributionBudget | None
    holdings_value: float = Field(gt=0)
    total_portfolio_value: float = Field(gt=0)
    largest_position_pct: float = Field(gt=0, le=100)
    sector_exposures: list[SectorExposure] = Field(min_length=1, max_length=21)
    portfolio_summary: str
    positions: list[ReviewedPosition] = Field(min_length=1, max_length=20)
    diversification_findings: list[PortfolioEvidence]
    scenario_assessment: ScenarioAssessment | None
    portfolio_risks: list[PortfolioEvidence]
    urgent_actions: list[PortfolioEvidence]
    review_triggers: list[PortfolioEvidence]
    tax_considerations: list[PortfolioEvidence]
    rebalance_assessment: RebalanceAssessment
    assumptions: list[str]
    warnings: list[str]
    sources: list[PortfolioSource]


class ReviewAction(_StrictModel):
    sequence: int = Field(ge=1, le=40)
    action_id: ActionId
    ticker: str
    display_name: str
    action: ReviewActionType
    priority: ReviewActionPriority
    timing: str
    target_weight_pct: float | None = Field(default=None, ge=0, le=100)
    sizing_basis: ActionSizingBasis
    sizing_pct: float | None = Field(default=None, gt=0, le=100)
    estimated_quantity: float | None = Field(default=None, gt=0)
    estimated_value: float | None = Field(default=None, ge=0)
    rationale: str
    dependencies: list[str] = Field(max_length=40)
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)
    source_scope: ActionSourceScope


class ReviewActionPlanPayload(_StrictModel):
    generated_at: datetime.datetime
    market_data_at: datetime.datetime
    market: str
    market_name: str
    currency: str
    basis: ActionPlanBasis
    summary: str
    actions: list[ReviewAction] = Field(min_length=1, max_length=40)
    warnings: list[str] = Field(max_length=10)


class RebalanceApplicationPayload(_StrictModel):
    generated_at: datetime.datetime
    research_as_of: datetime.date
    market_data_at: datetime.datetime
    market: str
    market_name: str
    currency: str
    available_cash: float = Field(ge=0)
    additional_budget: ContributionBudget | None
    research: RebalanceResearch
    plan: portfolio_rebalance_schemas.RebalancePlan
