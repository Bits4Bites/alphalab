from __future__ import annotations

import datetime
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

CATEGORY_WEIGHT_MAP = {
    "valuation": 20,
    "quality": 20,
    "growth": 15,
    "momentum": 15,
    "catalysts": 15,
    "risk_resilience": 15,
}
CATEGORY_NAMES = tuple(CATEGORY_WEIGHT_MAP)
METRIC_KEYS = (
    "size",
    "valuation",
    "growth",
    "income_yield",
    "volatility",
    "cost",
)
PROFILE_NAMES = ("conservative", "moderate", "aggressive")

CategoryName = Literal[
    "valuation",
    "quality",
    "growth",
    "momentum",
    "catalysts",
    "risk_resilience",
]
MetricKey = Literal["size", "valuation", "growth", "income_yield", "volatility", "cost"]
ProfileName = Literal["conservative", "moderate", "aggressive"]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class ResearchSource(_StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=120)
    url: AnyHttpUrl
    published_at: datetime.date | None


class MetricObservation(_StrictModel):
    key: MetricKey
    label: str = Field(min_length=1, max_length=80)
    display_value: str = Field(min_length=1, max_length=120)
    applicability: Literal["applicable", "not_applicable", "unavailable"]
    as_of: datetime.date | None
    source_ids: list[str] = Field(max_length=3)
    note: str = Field(max_length=300)

    @model_validator(mode="after")
    def validate_applicability(self) -> MetricObservation:
        if self.applicability == "applicable":
            if not self.source_ids or self.as_of is None:
                raise ValueError("applicable metrics require sources and an as-of date")
        elif self.applicability == "not_applicable":
            if self.source_ids or self.as_of is not None or self.display_value.upper() != "N/A":
                raise ValueError("not-applicable metrics must use N/A without sources or an as-of date")
        elif self.display_value.lower() not in {"unavailable", "not available"}:
            raise ValueError("unavailable metrics must be labelled unavailable")
        return self


class CategoryAssessment(_StrictModel):
    category: CategoryName
    score: int = Field(ge=0, le=100)
    confidence: Literal["high", "medium", "low"]
    summary: str = Field(min_length=1, max_length=600)
    evidence: list[ShortText] = Field(min_length=1, max_length=4)
    source_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("score", mode="before")
    @classmethod
    def reject_boolean_score(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("category score must be an integer")
        return value


class ProfileSuitability(_StrictModel):
    profile: ProfileName
    rating: Literal["strong_fit", "fit", "mixed", "poor_fit"]
    summary: str = Field(min_length=1, max_length=400)


class ScenarioAssessment(_StrictModel):
    impact: Literal["positive", "neutral", "mixed", "negative", "not_assessed"]
    resilience_score: int | None = Field(ge=0, le=100)
    summary: str = Field(min_length=1, max_length=600)
    key_drivers: list[ShortText] = Field(max_length=5)
    source_ids: list[str] = Field(max_length=6)

    @model_validator(mode="after")
    def validate_assessment(self) -> ScenarioAssessment:
        if self.impact == "not_assessed":
            if self.resilience_score is not None or self.source_ids:
                raise ValueError("an unassessed scenario cannot contain a score or sources")
        elif self.resilience_score is None or not self.source_ids:
            raise ValueError("an assessed scenario requires a resilience score and sources")
        return self


class CoreCandidateResearch(_StrictModel):
    ticker: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    asset_type: Literal["stock", "etf"]
    categories: list[CategoryAssessment] = Field(min_length=6, max_length=6)
    metrics: list[MetricObservation] = Field(min_length=6, max_length=6)
    profile_suitability: list[ProfileSuitability] = Field(min_length=3, max_length=3)
    strengths: list[ShortText] = Field(min_length=2, max_length=5)
    risks: list[ShortText] = Field(min_length=2, max_length=5)
    catalysts: list[ShortText] = Field(min_length=1, max_length=5)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper()
        if not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("ticker must be a bare market symbol")
        return normalized

    @model_validator(mode="after")
    def validate_complete_scorecard(self) -> CandidateResearch:
        categories = [assessment.category for assessment in self.categories]
        if len(set(categories)) != len(categories) or set(categories) != set(CATEGORY_NAMES):
            raise ValueError("each scorecard category must appear exactly once")

        metrics = [metric.key for metric in self.metrics]
        if len(set(metrics)) != len(metrics) or set(metrics) != set(METRIC_KEYS):
            raise ValueError("each comparison metric must appear exactly once")

        profiles = [suitability.profile for suitability in self.profile_suitability]
        if len(set(profiles)) != len(profiles) or set(profiles) != set(PROFILE_NAMES):
            raise ValueError("each investor profile must appear exactly once")
        return self


class CandidateResearch(CoreCandidateResearch):
    scenario: ScenarioAssessment


def _candidate_source_references(candidate: CoreCandidateResearch) -> set[str]:
    references = {
        source_id
        for category in candidate.categories
        for source_id in category.source_ids
    }
    references.update(
        source_id for metric in candidate.metrics for source_id in metric.source_ids
    )
    return references


class CoreComparisonResearch(_StrictModel):
    as_of: datetime.datetime
    methodology_summary: str = Field(min_length=1, max_length=1200)
    candidates: list[CoreCandidateResearch] = Field(min_length=2, max_length=5)
    sources: list[ResearchSource] = Field(min_length=2, max_length=30)
    caveats: list[ShortText] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_sources(self) -> CoreComparisonResearch:
        tickers = [candidate.ticker for candidate in self.candidates]
        if len(tickers) != len(set(tickers)):
            raise ValueError("candidate tickers must be unique")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        known_sources = set(source_ids)
        for candidate in self.candidates:
            if not _candidate_source_references(candidate).issubset(known_sources):
                raise ValueError(f"candidate {candidate.ticker} references an unknown source")
        return self


class CandidateScenarioResearch(_StrictModel):
    ticker: str = Field(min_length=1, max_length=20)
    scenario: ScenarioAssessment

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper()
        if not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("ticker must be a bare market symbol")
        return normalized


class ComparisonScenarioResearch(_StrictModel):
    scenario: str = Field(min_length=1, max_length=1000)
    as_of: datetime.datetime
    candidates: list[CandidateScenarioResearch] = Field(min_length=2, max_length=5)
    sources: list[ResearchSource] = Field(min_length=1, max_length=10)
    caveats: list[ShortText] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_sources(self) -> ComparisonScenarioResearch:
        tickers = [candidate.ticker for candidate in self.candidates]
        if len(tickers) != len(set(tickers)):
            raise ValueError("scenario candidate tickers must be unique")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("scenario source IDs must be unique")
        known_sources = set(source_ids)
        for candidate in self.candidates:
            if not set(candidate.scenario.source_ids).issubset(known_sources):
                raise ValueError(
                    f"scenario candidate {candidate.ticker} references an unknown source"
                )
        return self


class ComparisonResearch(_StrictModel):
    as_of: datetime.datetime
    methodology_summary: str = Field(min_length=1, max_length=1200)
    candidates: list[CandidateResearch] = Field(min_length=2, max_length=5)
    sources: list[ResearchSource] = Field(min_length=2, max_length=40)
    caveats: list[ShortText] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_sources(self) -> ComparisonResearch:
        tickers = [candidate.ticker for candidate in self.candidates]
        if len(tickers) != len(set(tickers)):
            raise ValueError("candidate tickers must be unique")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        known_sources = set(source_ids)

        for candidate in self.candidates:
            references = _candidate_source_references(candidate)
            references.update(candidate.scenario.source_ids)
            if not references.issubset(known_sources):
                raise ValueError(f"candidate {candidate.ticker} references an unknown source")
        return self


class CategoryWeight(_StrictModel):
    category: CategoryName
    weight_pct: int = Field(gt=0, le=100)


class CandidateSnapshot(_StrictModel):
    ticker: str
    name: str
    asset_type: Literal["stock", "etf"]
    current_price: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    quote_as_of: datetime.datetime


class RankingEntry(_StrictModel):
    ticker: str
    rank: int = Field(ge=1, le=5)
    weighted_score: float = Field(ge=0, le=100)
    confidence: Literal["high", "medium", "low"]


class CategoryWinner(_StrictModel):
    category: CategoryName
    score: int = Field(ge=0, le=100)
    tickers: list[str] = Field(min_length=1, max_length=5)


class ComparisonResult(_StrictModel):
    generated_at: datetime.datetime
    research_as_of: datetime.datetime
    market_data_at: datetime.datetime
    market: str
    market_name: str
    currency: str
    scenario: str
    methodology_summary: str
    category_weights: list[CategoryWeight] = Field(min_length=6, max_length=6)
    snapshots: list[CandidateSnapshot] = Field(min_length=2, max_length=5)
    rankings: list[RankingEntry] = Field(min_length=2, max_length=5)
    category_winners: list[CategoryWinner] = Field(min_length=6, max_length=6)
    candidates: list[CandidateResearch] = Field(min_length=2, max_length=5)
    sources: list[ResearchSource] = Field(min_length=2, max_length=40)
    caveats: list[ShortText] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_deterministic_result(self) -> ComparisonResult:
        ranking_tickers = [entry.ticker for entry in self.rankings]
        if len(ranking_tickers) != len(set(ranking_tickers)):
            raise ValueError("ranking tickers must be unique")
        if [snapshot.ticker for snapshot in self.snapshots] != ranking_tickers:
            raise ValueError("snapshot order must match ranking order")
        if [candidate.ticker for candidate in self.candidates] != ranking_tickers:
            raise ValueError("candidate order must match ranking order")

        weights = {weight.category: weight.weight_pct for weight in self.category_weights}
        if len(weights) != len(self.category_weights) or weights != CATEGORY_WEIGHT_MAP:
            raise ValueError("category weights must match the fixed comparison methodology")

        previous_score: float | None = None
        previous_rank = 0
        for index, ranking in enumerate(self.rankings, start=1):
            candidate = next(
                candidate
                for candidate in self.candidates
                if candidate.ticker == ranking.ticker
            )
            scores = {
                assessment.category: assessment.score
                for assessment in candidate.categories
            }
            expected_score = sum(
                Decimal(scores[category]) * Decimal(weight) / Decimal(100)
                for category, weight in CATEGORY_WEIGHT_MAP.items()
            )
            confidences = {assessment.confidence for assessment in candidate.categories}
            expected_confidence = (
                "low"
                if "low" in confidences
                else "high"
                if confidences == {"high"}
                else "medium"
            )
            if Decimal(str(ranking.weighted_score)) != expected_score:
                raise ValueError("weighted scores must match the fixed comparison methodology")
            if ranking.confidence != expected_confidence:
                raise ValueError("overall confidence must match category confidence")

            expected_rank = previous_rank if ranking.weighted_score == previous_score else index
            if ranking.rank != expected_rank:
                raise ValueError("ranking positions must follow deterministic tie rules")
            if previous_score is not None and ranking.weighted_score > previous_score:
                raise ValueError("rankings must be ordered by descending weighted score")
            previous_score = ranking.weighted_score
            previous_rank = ranking.rank

        winners = {winner.category: winner for winner in self.category_winners}
        if len(winners) != len(self.category_winners) or set(winners) != set(CATEGORY_NAMES):
            raise ValueError("each category winner must appear exactly once")
        candidate_by_ticker = {candidate.ticker: candidate for candidate in self.candidates}
        for category, winner in winners.items():
            scores = {
                ticker: next(
                    assessment.score
                    for assessment in candidate.categories
                    if assessment.category == category
                )
                for ticker, candidate in candidate_by_ticker.items()
            }
            winning_score = max(scores.values())
            winning_tickers = sorted(
                ticker for ticker, score in scores.items() if score == winning_score
            )
            if winner.score != winning_score or winner.tickers != winning_tickers:
                raise ValueError("category winners must match candidate scores")

        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("result source IDs must be unique")
        known_sources = set(source_ids)
        for candidate in self.candidates:
            references = {
                source_id
                for category in candidate.categories
                for source_id in category.source_ids
            }
            references.update(
                source_id
                for metric in candidate.metrics
                for source_id in metric.source_ids
            )
            references.update(candidate.scenario.source_ids)
            if not references.issubset(known_sources):
                raise ValueError(f"candidate {candidate.ticker} references an unknown result source")

        if any(snapshot.currency != self.currency for snapshot in self.snapshots):
            raise ValueError("all snapshots must use the comparison currency")
        scenario_was_requested = bool(self.scenario)
        if any(
            (candidate.scenario.impact != "not_assessed") != scenario_was_requested
            for candidate in self.candidates
        ):
            raise ValueError("scenario assessments must match the comparison request")
        return self


class ComparisonCachePayload(_StrictModel):
    result: ComparisonResult
