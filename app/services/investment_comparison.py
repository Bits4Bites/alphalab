from __future__ import annotations

import datetime
import json
import logging
import re
from decimal import Decimal
from typing import NoReturn

from pydantic import ValidationError

from app.schemas import investment_comparison as comparison_schemas
from app.services import portfolio_market_data

logger = logging.getLogger(__name__)

CATEGORY_WEIGHTS: tuple[tuple[str, int], ...] = tuple(comparison_schemas.CATEGORY_WEIGHT_MAP.items())
MIN_CANDIDATES = 2
MAX_CANDIDATES = 5
_TICKER_SPLIT_PATTERN = re.compile(r"[\s,;]+")


class ComparisonError(ValueError):
    pass


class ComparisonInputError(ComparisonError):
    pass


class ComparisonResearchError(ComparisonError):
    def __init__(
        self,
        message: str,
        *,
        repairable: bool = False,
        validation_issues: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.repairable = repairable
        self.validation_issues = validation_issues


def parse_tickers(value: str, market: portfolio_market_data.MarketDefinition) -> tuple[str, ...]:
    raw_tickers = [ticker for ticker in _TICKER_SPLIT_PATTERN.split(value.strip()) if ticker]
    if len(raw_tickers) < MIN_CANDIDATES or len(raw_tickers) > MAX_CANDIDATES:
        raise ComparisonInputError(f"Enter between {MIN_CANDIDATES} and {MAX_CANDIDATES} ticker symbols.")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_ticker in raw_tickers:
        try:
            ticker = portfolio_market_data.normalize_symbol(raw_ticker, market)
        except portfolio_market_data.MarketSymbolError as exc:
            raise ComparisonInputError(str(exc)) from exc
        if ticker in seen:
            raise ComparisonInputError(f"Ticker {ticker} is listed more than once.")
        seen.add(ticker)
        normalized.append(ticker)
    return tuple(normalized)


def market_prompt_data(
    tickers: tuple[str, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
) -> list[dict[str, object]]:
    return [
        {
            "ticker": ticker,
            "name": quotes[ticker].display_name or ticker,
            "asset_type": quotes[ticker].asset_type,
            "current_price": float(quotes[ticker].price),
            "currency": quotes[ticker].currency,
            "quote_as_of": quotes[ticker].retrieved_at.isoformat(),
        }
        for ticker in tickers
    ]


def research_schema() -> dict[str, object]:
    return comparison_schemas.ComparisonResearch.model_json_schema()


def core_research_schema() -> dict[str, object]:
    return comparison_schemas.CoreComparisonResearch.model_json_schema()


def scenario_research_schema() -> dict[str, object]:
    return comparison_schemas.ComparisonScenarioResearch.model_json_schema()


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _decode_json(value: str, *, invalid_message: str) -> object:
    try:
        return json.loads(value, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("%s", invalid_message)
        raise ComparisonResearchError(
            invalid_message,
            repairable=True,
            validation_issues=("response: invalid JSON",),
        ) from exc


def _structured_validation_error(
    exc: ValidationError,
    *,
    subject: str,
    message: str,
) -> ComparisonResearchError:
    issues = tuple(
        f"{'.'.join(str(part) for part in error['loc']) or 'response'}: {error['msg']}"
        for error in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:12]
    )
    logger.warning(
        "%s structured validation failed: %s",
        subject,
        " | ".join(issues),
    )
    return ComparisonResearchError(
        message,
        repairable=True,
        validation_issues=issues,
    )


def parse_research(value: str) -> comparison_schemas.ComparisonResearch:
    try:
        return comparison_schemas.ComparisonResearch.model_validate(
            _decode_json(
                value,
                invalid_message="The AI returned an invalid comparison response.",
            )
        )
    except ValidationError as exc:
        raise _structured_validation_error(
            exc,
            subject="Investment comparison",
            message="The AI comparison failed structured validation.",
        ) from exc


def parse_core_research(value: str) -> comparison_schemas.CoreComparisonResearch:
    try:
        return comparison_schemas.CoreComparisonResearch.model_validate(
            _decode_json(
                value,
                invalid_message="The AI returned an invalid comparison response.",
            )
        )
    except ValidationError as exc:
        raise _structured_validation_error(
            exc,
            subject="Investment comparison core research",
            message="The AI comparison failed structured validation.",
        ) from exc


def parse_scenario_research(value: str) -> comparison_schemas.ComparisonScenarioResearch:
    try:
        return comparison_schemas.ComparisonScenarioResearch.model_validate(
            _decode_json(
                value,
                invalid_message="The AI returned an invalid scenario response.",
            )
        )
    except ValidationError as exc:
        raise _structured_validation_error(
            exc,
            subject="Investment comparison scenario research",
            message="The AI scenario analysis failed structured validation.",
        ) from exc


def _validate_research_time(
    as_of: datetime.datetime,
    sources: list[comparison_schemas.ResearchSource],
    *,
    subject: str,
) -> None:
    if as_of.tzinfo is None:
        raise ComparisonResearchError(f"The AI {subject} as-of timestamp must include a timezone.")
    if as_of > datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5):
        raise ComparisonResearchError(f"The AI {subject} as-of timestamp cannot be in the future.")

    today = datetime.datetime.now(datetime.UTC).date()
    if any(source.published_at and source.published_at > today for source in sources):
        raise ComparisonResearchError(f"The AI {subject} contains a future-dated source.")


def _normalize_core_candidate_sources(
    candidate: comparison_schemas.CoreCandidateResearch,
    known_source_ids: set[str],
) -> tuple[comparison_schemas.CoreCandidateResearch, int]:
    removed_count = 0
    unsourced_category_count = 0
    categories: list[comparison_schemas.CategoryAssessment] = []
    for assessment in candidate.categories:
        source_ids = [
            source_id
            for source_id in assessment.source_ids
            if source_id in known_source_ids
        ]
        removed_count += len(assessment.source_ids) - len(source_ids)
        update: dict[str, object] = {"source_ids": source_ids}
        if not source_ids:
            unsourced_category_count += 1
            update.update(
                {
                    "score": comparison_schemas.NEUTRAL_UNSOURCED_CATEGORY_SCORE,
                    "confidence": "low",
                    "summary": (
                        "Returned source unavailable; backend normalized this category "
                        f"to a neutral score. {assessment.summary}"
                    )[:600],
                }
            )
        categories.append(assessment.model_copy(update=update))

    if unsourced_category_count > comparison_schemas.MAX_UNSOURCED_CATEGORIES:
        issue = (
            f"candidate {candidate.ticker} has {unsourced_category_count} categories "
            "without returned sources"
        )
        raise ComparisonResearchError(
            f"The AI comparison for {candidate.ticker} has insufficient source coverage.",
            repairable=True,
            validation_issues=(issue,),
        )

    metrics: list[comparison_schemas.MetricObservation] = []
    for metric in candidate.metrics:
        source_ids = [
            source_id for source_id in metric.source_ids if source_id in known_source_ids
        ]
        removed_count += len(metric.source_ids) - len(source_ids)
        update = {"source_ids": source_ids}
        if metric.applicability == "applicable" and not source_ids:
            update.update(
                {
                    "display_value": "Unavailable",
                    "applicability": "unavailable",
                    "as_of": None,
                    "note": "No returned source matched the AI citation.",
                }
            )
        metrics.append(metric.model_copy(update=update))

    normalized = candidate.model_copy(
        update={
            "categories": categories,
            "metrics": metrics,
        }
    )
    return (
        comparison_schemas.CoreCandidateResearch.model_validate(
            normalized.model_dump(mode="python")
        ),
        removed_count,
    )


def validate_core_research(
    research: comparison_schemas.CoreComparisonResearch,
    *,
    tickers: tuple[str, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    market: portfolio_market_data.MarketDefinition,
) -> comparison_schemas.CoreComparisonResearch:
    _validate_research_time(
        research.as_of,
        research.sources,
        subject="comparison",
    )

    expected = set(tickers)
    returned = {candidate.ticker for candidate in research.candidates}
    if returned != expected:
        raise ComparisonResearchError("The AI comparison did not return the exact requested ticker set.")

    normalized_candidates: list[comparison_schemas.CoreCandidateResearch] = []
    by_ticker = {candidate.ticker: candidate for candidate in research.candidates}
    known_source_ids = {source.id for source in research.sources}
    for ticker in tickers:
        candidate = by_ticker[ticker]
        quote = quotes.get(ticker)
        if quote is None:
            raise ComparisonResearchError(f"Validated market data is missing for {ticker}.")
        if candidate.asset_type != quote.asset_type:
            raise ComparisonResearchError(f"The AI asset type for {ticker} does not match validated market data.")
        candidate, removed_count = _normalize_core_candidate_sources(
            candidate,
            known_source_ids,
        )
        if removed_count:
            logger.warning(
                "Removed %d unresolved source reference(s) from core research for %s.",
                removed_count,
                ticker,
            )
        normalized_candidates.append(candidate.model_copy(update={"name": quote.display_name or candidate.name}))

    if any(quote.currency != market.currency for quote in quotes.values()):
        raise ComparisonResearchError("Validated quotes do not use the selected market currency.")
    return research.model_copy(update={"candidates": normalized_candidates})


def validate_scenario_research(
    research: comparison_schemas.ComparisonScenarioResearch,
    *,
    tickers: tuple[str, ...],
    scenario: str,
) -> comparison_schemas.ComparisonScenarioResearch:
    cleaned_scenario = scenario.strip()
    if not cleaned_scenario or research.scenario != cleaned_scenario:
        raise ComparisonResearchError("The AI scenario analysis does not match the comparison request.")

    _validate_research_time(
        research.as_of,
        research.sources,
        subject="scenario analysis",
    )
    expected = set(tickers)
    returned = {candidate.ticker for candidate in research.candidates}
    if returned != expected:
        raise ComparisonResearchError("The AI scenario analysis did not return the exact requested ticker set.")
    if any(candidate.scenario.impact == "not_assessed" for candidate in research.candidates):
        raise ComparisonResearchError("The AI scenario assessments do not match the comparison request.")

    by_ticker = {candidate.ticker: candidate for candidate in research.candidates}
    return research.model_copy(update={"candidates": [by_ticker[ticker] for ticker in tickers]})


def combine_research(
    core_research: comparison_schemas.CoreComparisonResearch,
    scenario_research: comparison_schemas.ComparisonScenarioResearch | None = None,
) -> comparison_schemas.ComparisonResearch:
    combined_sources = list(core_research.sources)
    combined_caveats = list(core_research.caveats)
    combined_as_of = core_research.as_of

    if scenario_research is None:
        scenarios = {
            candidate.ticker: comparison_schemas.ScenarioAssessment(
                impact="not_assessed",
                resilience_score=None,
                summary="No stress scenario was requested.",
                key_drivers=[],
                source_ids=[],
            )
            for candidate in core_research.candidates
        }
    else:
        source_ids_by_url = {str(source.url): source.id for source in core_research.sources}
        used_source_ids = {source.id for source in core_research.sources}
        remapped_source_ids: dict[str, str] = {}
        for index, source in enumerate(scenario_research.sources, start=1):
            existing_source_id = source_ids_by_url.get(str(source.url))
            if existing_source_id:
                remapped_source_ids[source.id] = existing_source_id
                continue

            new_source_id = f"SCN{index}"
            while new_source_id in used_source_ids:
                new_source_id = f"{new_source_id}_"
            used_source_ids.add(new_source_id)
            source_ids_by_url[str(source.url)] = new_source_id
            remapped_source_ids[source.id] = new_source_id
            combined_sources.append(source.model_copy(update={"id": new_source_id}))

        scenarios = {
            candidate.ticker: candidate.scenario.model_copy(
                update={"source_ids": [remapped_source_ids[source_id] for source_id in candidate.scenario.source_ids]}
            )
            for candidate in scenario_research.candidates
        }
        combined_caveats.extend(caveat for caveat in scenario_research.caveats if caveat not in combined_caveats)
        combined_as_of = max(combined_as_of, scenario_research.as_of)

    candidates = [
        comparison_schemas.CandidateResearch.model_validate(
            {
                **candidate.model_dump(mode="python"),
                "scenario": scenarios[candidate.ticker].model_dump(mode="python"),
            }
        )
        for candidate in core_research.candidates
    ]
    return comparison_schemas.ComparisonResearch(
        as_of=combined_as_of,
        methodology_summary=core_research.methodology_summary,
        candidates=candidates,
        sources=combined_sources,
        caveats=combined_caveats,
    )


def validate_research(
    research: comparison_schemas.ComparisonResearch,
    *,
    tickers: tuple[str, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    market: portfolio_market_data.MarketDefinition,
    scenario: str,
) -> comparison_schemas.ComparisonResearch:
    _validate_research_time(
        research.as_of,
        research.sources,
        subject="comparison",
    )

    expected = set(tickers)
    returned = {candidate.ticker for candidate in research.candidates}
    if returned != expected:
        raise ComparisonResearchError("The AI comparison did not return the exact requested ticker set.")

    scenario_was_requested = bool(scenario.strip())
    for candidate in research.candidates:
        scenario_was_assessed = candidate.scenario.impact != "not_assessed"
        if scenario_was_assessed != scenario_was_requested:
            raise ComparisonResearchError("The AI scenario assessments do not match the comparison request.")

    normalized_candidates: list[comparison_schemas.CandidateResearch] = []
    by_ticker = {candidate.ticker: candidate for candidate in research.candidates}
    for ticker in tickers:
        candidate = by_ticker[ticker]
        quote = quotes.get(ticker)
        if quote is None:
            raise ComparisonResearchError(f"Validated market data is missing for {ticker}.")
        if candidate.asset_type != quote.asset_type:
            raise ComparisonResearchError(f"The AI asset type for {ticker} does not match validated market data.")
        normalized_candidates.append(candidate.model_copy(update={"name": quote.display_name or candidate.name}))

    if any(quote.currency != market.currency for quote in quotes.values()):
        raise ComparisonResearchError("Validated quotes do not use the selected market currency.")
    return research.model_copy(update={"candidates": normalized_candidates})


def _overall_confidence(candidate: comparison_schemas.CandidateResearch) -> str:
    confidences = {assessment.confidence for assessment in candidate.categories}
    if "low" in confidences:
        return "low"
    if confidences == {"high"}:
        return "high"
    return "medium"


def _weighted_score(candidate: comparison_schemas.CandidateResearch) -> Decimal:
    scores = {assessment.category: assessment.score for assessment in candidate.categories}
    return sum(Decimal(scores[category]) * Decimal(weight) / Decimal(100) for category, weight in CATEGORY_WEIGHTS)


def build_result(
    research: comparison_schemas.ComparisonResearch,
    *,
    tickers: tuple[str, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    market: portfolio_market_data.MarketDefinition,
    scenario: str,
) -> comparison_schemas.ComparisonResult:
    by_ticker = {candidate.ticker: candidate for candidate in research.candidates}
    scored = sorted(
        ((ticker, _weighted_score(by_ticker[ticker]), _overall_confidence(by_ticker[ticker])) for ticker in tickers),
        key=lambda item: (-item[1], item[0]),
    )

    rankings: list[comparison_schemas.RankingEntry] = []
    previous_score: Decimal | None = None
    previous_rank = 0
    for index, (ticker, score, confidence) in enumerate(scored, start=1):
        rank = previous_rank if score == previous_score else index
        rankings.append(
            comparison_schemas.RankingEntry(
                ticker=ticker,
                rank=rank,
                weighted_score=float(score),
                confidence=confidence,
            )
        )
        previous_score = score
        previous_rank = rank

    category_winners: list[comparison_schemas.CategoryWinner] = []
    for category, _ in CATEGORY_WEIGHTS:
        category_scores = {
            ticker: next(
                assessment.score for assessment in by_ticker[ticker].categories if assessment.category == category
            )
            for ticker in tickers
        }
        winning_score = max(category_scores.values())
        category_winners.append(
            comparison_schemas.CategoryWinner(
                category=category,
                score=winning_score,
                tickers=sorted(ticker for ticker, score in category_scores.items() if score == winning_score),
            )
        )

    ranking_order = [ranking.ticker for ranking in rankings]
    return comparison_schemas.ComparisonResult(
        generated_at=datetime.datetime.now(datetime.UTC),
        research_as_of=research.as_of,
        market_data_at=max(quote.retrieved_at for quote in quotes.values()),
        market=market.code,
        market_name=market.name,
        currency=market.currency,
        scenario=scenario,
        methodology_summary=research.methodology_summary,
        category_weights=[
            comparison_schemas.CategoryWeight(category=category, weight_pct=weight)
            for category, weight in CATEGORY_WEIGHTS
        ],
        snapshots=[
            comparison_schemas.CandidateSnapshot(
                ticker=ticker,
                name=quotes[ticker].display_name or ticker,
                asset_type=quotes[ticker].asset_type,
                current_price=float(quotes[ticker].price),
                currency=quotes[ticker].currency,
                quote_as_of=quotes[ticker].retrieved_at,
            )
            for ticker in ranking_order
        ],
        rankings=rankings,
        category_winners=category_winners,
        candidates=[by_ticker[ticker] for ticker in ranking_order],
        sources=research.sources,
        caveats=research.caveats,
    )


def cache_payload(result: comparison_schemas.ComparisonResult) -> dict[str, object]:
    return comparison_schemas.ComparisonCachePayload(result=result).model_dump(mode="json")


def is_valid_cache_payload(value: object) -> bool:
    try:
        comparison_schemas.ComparisonCachePayload.model_validate(value)
    except ValidationError:
        return False
    return True
