import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import review_portfolio as review_portfolio_schemas
from app.services import (
    analysis_cache,
    build_portfolio,
    portfolio_market_data,
    portfolio_rebalance,
    review_portfolio,
)
from app.utils import ai

router = APIRouter(tags=["review_portfolio"])
TEMPLATE = "review_portfolio.html"
_REVIEW_TEMPLATE = "partials/review_portfolio_report.html"
_REBALANCE_TEMPLATE = "partials/portfolio_rebalance_plan.html"
_CACHE_FEATURE = "review-portfolio"
_REBALANCE_CACHE_FEATURE = "review-portfolio-rebalance"
_CACHE_INPUT_FIELDS = (
    "holdings",
    "risk_tolerance",
    "investment_goals",
    "target_market",
    "investment_horizon",
    "scenario",
    "include_rebalance",
    "available_cash",
    "additional_budget",
    "allow_fractional_shares",
    "minimum_trade_amount",
    "tax_context",
)
_REBALANCE_CACHE_INPUT_FIELDS = _CACHE_INPUT_FIELDS
_REVIEW_PROMPT_TASK_ID = "REVIEW_PORTFOLIO_BUILD_PROMPT"
_REVIEW_RESEARCH_TASK_ID = "REVIEW_PORTFOLIO_ANALYZE"
_REBALANCE_PROMPT_TASK_ID = "REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT"
_REBALANCE_RESEARCH_TASK_ID = "REVIEW_PORTFOLIO_REBALANCE_ANALYZE"
logger = logging.getLogger(__name__)


def _render_review_html(payload: review_portfolio_schemas.ReviewPortfolioPayload) -> str:
    template = templating.templates.get_template(_REVIEW_TEMPLATE)
    return template.render(report=payload.model_dump(mode="json"))


def _render_rebalance_html(payload: review_portfolio_schemas.RebalanceApplicationPayload) -> str:
    template = templating.templates.get_template(_REBALANCE_TEMPLATE)
    return template.render(result=payload.model_dump(mode="json"))


def _matching_cached_inputs(
    first: dict[str, object],
    second: dict[str, object],
) -> bool:
    return all(first.get(field) == second.get(field) for field in _CACHE_INPUT_FIELDS)


@router.get("/review-portfolio", response_class=HTMLResponse)
async def review_portfolio_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    try:
        markets = portfolio_market_data.configured_markets(config.app_settings.primary_markets)
    except portfolio_market_data.MarketConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cached_review, cached_rebalance = await asyncio.gather(
        analysis_cache.get_cached_payload(
            user,
            feature=_CACHE_FEATURE,
            input_fields=_CACHE_INPUT_FIELDS,
            payload_validator=review_portfolio.is_valid_review_cache_payload,
        ),
        analysis_cache.get_cached_payload(
            user,
            feature=_REBALANCE_CACHE_FEATURE,
            input_fields=_REBALANCE_CACHE_INPUT_FIELDS,
            payload_validator=review_portfolio.is_valid_rebalance_cache_payload,
        ),
    )
    if cached_rebalance is not None and (
        cached_review is None or not _matching_cached_inputs(cached_review, cached_rebalance)
    ):
        cached_rebalance = None

    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {
            "user": user,
            "cached_review": cached_review,
            "cached_rebalance": cached_rebalance,
            "market_options": markets,
            "market_codes": [market.code for market in markets],
            "market_currencies": {market.code: market.currency for market in markets},
            "default_market": markets[0].code,
        },
    )


@router.post("/review-portfolio/stream")
async def review_portfolio_stream(
    body: review_portfolio_schemas.ReviewPortfolioRequest,
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def event(event_type: str, **data: object) -> dict[str, str]:
            return {"data": json.dumps({"type": event_type, **data})}

        total_steps = 10 if body.include_rebalance else 6
        yield event("progress", step=1, total=total_steps, message="Validating portfolio inputs...")

        try:
            market = portfolio_market_data.resolve_configured_market(
                body.target_market,
                config.app_settings.primary_markets,
            )
            holdings = portfolio_rebalance.parse_holdings(body.holdings, market)
            settings = portfolio_rebalance.parse_settings(
                available_cash=body.available_cash,
                fractional_shares=body.allow_fractional_shares,
                minimum_trade_amount=body.minimum_trade_amount,
                tax_context=body.tax_context,
            )
            budget = build_portfolio.parse_budget(body.additional_budget, market)
            review_portfolio.planning_settings(settings, budget)
        except (
            build_portfolio.BudgetInputError,
            portfolio_market_data.MarketConfigurationError,
            portfolio_market_data.MarketSymbolError,
            portfolio_rebalance.RebalanceInputError,
        ) as exc:
            yield event("error", message=str(exc))
            return

        yield event("progress", step=2, total=total_steps, message="Verifying holdings and current market data...")
        try:
            quotes = await portfolio_market_data.fetch_quotes(
                tuple(holding.ticker for holding in holdings),
                market,
            )
            snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, settings.available_cash)
        except (portfolio_market_data.MarketDataError, portfolio_rebalance.RebalanceCalculationError):
            logger.warning("Review Portfolio could not build a verified holdings snapshot")
            yield event("error", message="Current holdings could not be verified. Check the tickers and try again.")
            return

        review_prompt_client = config.ai_task_settings.get_ai_client(_REVIEW_PROMPT_TASK_ID)
        review_prompt_task = config.ai_task_settings.tasks.get(_REVIEW_PROMPT_TASK_ID)
        review_client = config.ai_task_settings.get_ai_client(_REVIEW_RESEARCH_TASK_ID)
        review_task = config.ai_task_settings.tasks.get(_REVIEW_RESEARCH_TASK_ID)
        if not review_prompt_client or not review_prompt_task or not review_client or not review_task:
            yield event("error", message="Portfolio Review is temporarily unavailable.")
            return

        yield event("progress", step=3, total=total_steps, message="Writing an adaptive portfolio-review prompt...")
        prompt_result = await ai.execute_task_prompt(
            review_prompt_client,
            review_prompt_task,
            review_portfolio.build_review_prompt_writer_request(
                body,
                market,
                settings,
                budget,
                snapshot,
            ),
        )
        if not prompt_result.success:
            logger.warning("Review Portfolio prompt-writing task failed")
            yield event("error", message="The portfolio-review prompt could not be prepared. Please try again.")
            return
        try:
            adaptive_review_prompt = review_portfolio.validate_adaptive_prompt(prompt_result.completion)
        except review_portfolio.AdaptivePromptError:
            yield event("error", message="The AI returned an invalid portfolio-review prompt. Please try again.")
            return

        review_research_prompt = review_portfolio.build_review_research_prompt(
            adaptive_review_prompt,
            body,
            market,
            settings,
            budget,
            snapshot,
        )
        yield event("progress", step=4, total=total_steps, message="Researching the current portfolio...")
        research_result = await ai.execute_task_prompt(
            review_client,
            review_task,
            review_research_prompt,
            response_json_schema=review_portfolio.review_response_schema(),
            schema_name="portfolio_review_research",
        )
        if not research_result.success:
            logger.warning("Review Portfolio premium research task failed")
            yield event("error", message="Portfolio research failed. Please try again.")
            return

        yield event("progress", step=5, total=total_steps, message="Validating the portfolio diagnosis and evidence...")
        current_tickers = tuple(holding.ticker for holding in holdings)
        review_issue: str | None = None
        try:
            review_report = review_portfolio.parse_review_research(
                research_result.completion,
                market,
                current_tickers,
                body.scenario,
            )
        except review_portfolio.ReviewResearchError as exc:
            review_issue = str(exc)
            logger.warning("Review Portfolio research validation failed: %s", review_issue)

        if review_issue is not None:
            correction_result = await ai.execute_task_prompt(
                review_client,
                review_task,
                review_portfolio.build_correction_prompt(
                    review_research_prompt,
                    research_result.completion,
                    review_issue,
                    stage="portfolio-review research",
                ),
                response_json_schema=review_portfolio.review_response_schema(),
                schema_name="portfolio_review_research",
            )
            if not correction_result.success:
                logger.warning("Review Portfolio correction task failed")
                yield event("error", message="The portfolio review could not be verified. Please try again.")
                return
            try:
                review_report = review_portfolio.parse_review_research(
                    correction_result.completion,
                    market,
                    current_tickers,
                    body.scenario,
                )
            except review_portfolio.ReviewResearchError as exc:
                logger.warning("Review Portfolio rejected the corrected research: %s", exc)
                yield event("error", message="The portfolio review could not be verified. Please try again.")
                return

        review_payload = review_portfolio.build_review_payload(
            body,
            market,
            settings,
            budget,
            snapshot,
            review_report,
        )
        cache_inputs = review_portfolio.cache_inputs(body, market, settings)
        await analysis_cache.set_cached_payload(
            user,
            feature=_CACHE_FEATURE,
            inputs=cache_inputs,
            payload=review_payload.model_dump(mode="json"),
            ttl_seconds=review_portfolio.REVIEW_CACHE_TTL_SECONDS,
        )
        yield event("review_result", html=_render_review_html(review_payload))

        if not body.include_rebalance:
            yield event("progress", step=6, total=total_steps, message="Portfolio review complete!")
            yield event("complete", status="review_only")
            return

        if review_report.rebalance_assessment.need == "none":
            yield event(
                "progress",
                step=total_steps,
                total=total_steps,
                message="Review complete; no meaningful rebalance was recommended.",
            )
            yield event(
                "rebalance_skipped",
                message="The validated review found no meaningful rebalance need, so no allocation plan was generated.",
            )
            yield event("complete", status="not_needed")
            return

        rebalance_prompt_client = config.ai_task_settings.get_ai_client(_REBALANCE_PROMPT_TASK_ID)
        rebalance_prompt_task = config.ai_task_settings.tasks.get(_REBALANCE_PROMPT_TASK_ID)
        rebalance_client = config.ai_task_settings.get_ai_client(_REBALANCE_RESEARCH_TASK_ID)
        rebalance_task = config.ai_task_settings.tasks.get(_REBALANCE_RESEARCH_TASK_ID)
        if not rebalance_prompt_client or not rebalance_prompt_task or not rebalance_client or not rebalance_task:
            yield event("rebalance_error", message="Rebalance planning is temporarily unavailable.")
            yield event("complete", status="rebalance_failed")
            return

        yield event("progress", step=6, total=total_steps, message="Writing a focused rebalance-research prompt...")
        rebalance_prompt_result = await ai.execute_task_prompt(
            rebalance_prompt_client,
            rebalance_prompt_task,
            review_portfolio.build_rebalance_prompt_writer_request(
                body,
                market,
                settings,
                budget,
                snapshot,
                review_report,
            ),
        )
        if not rebalance_prompt_result.success:
            logger.warning("Portfolio Rebalance prompt-writing task failed")
            yield event("rebalance_error", message="The rebalance prompt could not be prepared. Please try again.")
            yield event("complete", status="rebalance_failed")
            return
        try:
            adaptive_rebalance_prompt = review_portfolio.validate_adaptive_prompt(rebalance_prompt_result.completion)
        except review_portfolio.AdaptivePromptError:
            yield event("rebalance_error", message="The AI returned an invalid rebalance prompt. Please try again.")
            yield event("complete", status="rebalance_failed")
            return

        rebalance_research_prompt = review_portfolio.build_rebalance_research_prompt(
            adaptive_rebalance_prompt,
            body,
            market,
            settings,
            budget,
            snapshot,
            review_report,
        )
        yield event("progress", step=7, total=total_steps, message="Researching a target allocation...")
        allocation_result = await ai.execute_task_prompt(
            rebalance_client,
            rebalance_task,
            rebalance_research_prompt,
            response_json_schema=review_portfolio.rebalance_response_schema(),
            schema_name="portfolio_rebalance_research",
        )
        if not allocation_result.success:
            logger.warning("Portfolio Rebalance premium research task failed")
            yield event("rebalance_error", message="Target-allocation research failed. Please try again.")
            yield event("complete", status="rebalance_failed")
            return

        yield event(
            "progress",
            step=8,
            total=total_steps,
            message="Validating allocations, evidence, and securities...",
        )
        rebalance_issue: str | None = None
        try:
            rebalance_report = review_portfolio.parse_rebalance_research(
                allocation_result.completion,
                market,
            )
            review_portfolio.validate_rebalance_alignment(
                rebalance_report,
                review_report,
                snapshot,
                budget,
            )
            all_quotes = dict(quotes)
            missing_tickers = tuple(
                ticker
                for ticker in review_portfolio.recommendation_tickers(rebalance_report)
                if ticker not in all_quotes
            )
            if missing_tickers:
                all_quotes.update(await portfolio_market_data.fetch_quotes(missing_tickers, market))
        except (review_portfolio.RebalanceResearchError, portfolio_market_data.MarketDataError) as exc:
            rebalance_issue = str(exc)
            logger.warning("Portfolio Rebalance research validation failed: %s", rebalance_issue)

        if rebalance_issue is not None:
            correction_result = await ai.execute_task_prompt(
                rebalance_client,
                rebalance_task,
                review_portfolio.build_correction_prompt(
                    rebalance_research_prompt,
                    allocation_result.completion,
                    rebalance_issue,
                    stage="portfolio-rebalance research",
                ),
                response_json_schema=review_portfolio.rebalance_response_schema(),
                schema_name="portfolio_rebalance_research",
            )
            if not correction_result.success:
                logger.warning("Portfolio Rebalance correction task failed")
                yield event("rebalance_error", message="The target allocation could not be verified. Please try again.")
                yield event("complete", status="rebalance_failed")
                return
            try:
                rebalance_report = review_portfolio.parse_rebalance_research(
                    correction_result.completion,
                    market,
                )
                review_portfolio.validate_rebalance_alignment(
                    rebalance_report,
                    review_report,
                    snapshot,
                    budget,
                )
                all_quotes = dict(quotes)
                missing_tickers = tuple(
                    ticker
                    for ticker in review_portfolio.recommendation_tickers(rebalance_report)
                    if ticker not in all_quotes
                )
                if missing_tickers:
                    all_quotes.update(await portfolio_market_data.fetch_quotes(missing_tickers, market))
            except (review_portfolio.RebalanceResearchError, portfolio_market_data.MarketDataError) as exc:
                logger.warning("Portfolio Rebalance rejected the corrected research: %s", exc)
                yield event("rebalance_error", message="The target allocation could not be verified. Please try again.")
                yield event("complete", status="rebalance_failed")
                return

        yield event("progress", step=9, total=total_steps, message="Calculating feasible rebalance trades...")
        try:
            plan_settings = review_portfolio.planning_settings(settings, budget)
            plan_snapshot = portfolio_rebalance.build_snapshot(
                holdings,
                quotes,
                plan_settings.available_cash,
            )
            recommendation = review_portfolio.to_plan_recommendation(rebalance_report)
            plan = portfolio_rebalance.calculate_plan(
                plan_snapshot,
                recommendation,
                all_quotes,
                market,
                plan_settings,
            )
            plan = review_portfolio.apply_budget_warnings(plan, budget)
            review_portfolio.validate_plan_alignment(plan, review_report)
            rebalance_payload = review_portfolio.build_rebalance_payload(
                market,
                settings,
                budget,
                rebalance_report,
                plan,
            )
        except (
            portfolio_rebalance.RebalanceCalculationError,
            portfolio_rebalance.RebalanceInputError,
            ValidationError,
        ):
            logger.warning("Portfolio Rebalance deterministic plan validation failed")
            yield event(
                "rebalance_error",
                message="A feasible rebalance plan could not be calculated. Please try again.",
            )
            yield event("complete", status="rebalance_failed")
            return

        await analysis_cache.set_cached_payload(
            user,
            feature=_REBALANCE_CACHE_FEATURE,
            inputs=cache_inputs,
            payload=rebalance_payload.model_dump(mode="json"),
            ttl_seconds=review_portfolio.REBALANCE_CACHE_TTL_SECONDS,
        )
        yield event(
            "rebalance_result",
            html=_render_rebalance_html(rebalance_payload),
            plan=plan.model_dump(mode="json"),
        )
        yield event("progress", step=10, total=total_steps, message="Review and rebalance plan complete!")
        yield event("complete", status="success")

    return EventSourceResponse(event_generator())
