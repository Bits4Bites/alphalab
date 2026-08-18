import asyncio
import datetime
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
_ACTION_TEMPLATE = "partials/review_portfolio_action_plan.html"
_CACHE_FEATURE = "review-portfolio"
_REBALANCE_CACHE_FEATURE = "review-portfolio-rebalance"
_ACTION_CACHE_FEATURE = "review-portfolio-action-plan"
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
_ACTION_PROMPT_TASK_ID = "REVIEW_PORTFOLIO_ACTION_BUILD_PROMPT"
_ACTION_PLAN_TASK_ID = "REVIEW_PORTFOLIO_ACTION_PLAN"
logger = logging.getLogger(__name__)


def _render_review_html(payload: review_portfolio_schemas.ReviewPortfolioPayload) -> str:
    template = templating.templates.get_template(_REVIEW_TEMPLATE)
    return template.render(report=payload.model_dump(mode="json"))


def _render_rebalance_html(payload: review_portfolio_schemas.RebalanceApplicationPayload) -> str:
    template = templating.templates.get_template(_REBALANCE_TEMPLATE)
    return template.render(result=payload.model_dump(mode="json"))


def _render_action_html(payload: review_portfolio_schemas.ReviewActionPlanPayload) -> str:
    template = templating.templates.get_template(_ACTION_TEMPLATE)
    return template.render(action_plan=payload.model_dump(mode="json"))


def _matching_cached_inputs(
    first: dict[str, object],
    second: dict[str, object],
) -> bool:
    return all(first.get(field) == second.get(field) for field in _CACHE_INPUT_FIELDS)


def _cache_is_at_least_as_new(
    candidate: dict[str, object],
    parent: dict[str, object],
) -> bool:
    candidate_value = candidate.get("generated_at")
    parent_value = parent.get("generated_at")
    if not isinstance(candidate_value, str) or not isinstance(parent_value, str):
        return False
    try:
        candidate_at = datetime.datetime.fromisoformat(candidate_value)
        parent_at = datetime.datetime.fromisoformat(parent_value)
    except ValueError:
        return False
    if candidate_at.tzinfo is None:
        candidate_at = candidate_at.replace(tzinfo=datetime.UTC)
    if parent_at.tzinfo is None:
        parent_at = parent_at.replace(tzinfo=datetime.UTC)
    return candidate_at >= parent_at


async def _generate_action_plan(
    body: review_portfolio_schemas.ReviewPortfolioRequest,
    market: portfolio_market_data.MarketDefinition,
    settings: portfolio_rebalance.RebalanceSettings,
    budget: build_portfolio.BudgetPlan | None,
    snapshot: portfolio_rebalance.PortfolioSnapshot,
    review_report: review_portfolio_schemas.PortfolioReviewResearch,
    candidates: list[review_portfolio_schemas.ReviewActionCandidate],
    *,
    basis: review_portfolio_schemas.ActionPlanBasis,
    market_data_at: datetime.datetime | None = None,
) -> review_portfolio_schemas.ReviewActionPlanPayload:
    action_prompt_client = config.ai_task_settings.get_ai_client(_ACTION_PROMPT_TASK_ID)
    action_prompt_task = config.ai_task_settings.tasks.get(_ACTION_PROMPT_TASK_ID)
    action_client = config.ai_task_settings.get_ai_client(_ACTION_PLAN_TASK_ID)
    action_task = config.ai_task_settings.tasks.get(_ACTION_PLAN_TASK_ID)
    if not action_prompt_client or not action_prompt_task or not action_client or not action_task:
        raise review_portfolio.ActionPlanError("Portfolio action planning is temporarily unavailable.")

    prompt_result = await ai.execute_task_prompt(
        action_prompt_client,
        action_prompt_task,
        review_portfolio.build_action_prompt_writer_request(
            body,
            market,
            settings,
            budget,
            snapshot,
            review_report,
            candidates,
            basis=basis,
        ),
    )
    if not prompt_result.success:
        logger.warning("Review Portfolio action prompt-writing task failed")
        raise review_portfolio.ActionPlanError("The portfolio action prompt could not be prepared.")
    try:
        adaptive_prompt = review_portfolio.validate_adaptive_prompt(prompt_result.completion)
    except review_portfolio.AdaptivePromptError as exc:
        raise review_portfolio.ActionPlanError("The AI returned an invalid portfolio action prompt.") from exc

    action_prompt = review_portfolio.build_action_research_prompt(
        adaptive_prompt,
        body,
        market,
        settings,
        budget,
        snapshot,
        review_report,
        candidates,
        basis=basis,
    )
    action_result = await ai.execute_task_prompt(
        action_client,
        action_task,
        action_prompt,
        response_json_schema=review_portfolio.action_plan_response_schema(),
        schema_name="review_portfolio_action_plan",
    )
    if not action_result.success:
        logger.warning("Review Portfolio action-planning task failed")
        raise review_portfolio.ActionPlanError("Portfolio action planning failed.")

    def parse_and_build(value: str) -> review_portfolio_schemas.ReviewActionPlanPayload:
        research = review_portfolio.parse_action_plan_research(value, candidates)
        return review_portfolio.build_action_plan_payload(
            market,
            settings,
            budget,
            snapshot,
            candidates,
            research,
            basis=basis,
            market_data_at=market_data_at,
        )

    try:
        return parse_and_build(action_result.completion)
    except review_portfolio.ActionPlanError as exc:
        action_issue = str(exc)
        logger.warning("Review Portfolio action-plan validation failed: %s", action_issue)

    correction_result = await ai.execute_task_prompt(
        action_client,
        action_task,
        review_portfolio.build_action_correction_prompt(
            action_prompt,
            action_result.completion,
            action_issue,
        ),
        response_json_schema=review_portfolio.action_plan_response_schema(),
        schema_name="review_portfolio_action_plan",
    )
    if not correction_result.success:
        logger.warning("Review Portfolio action-plan correction task failed")
        raise review_portfolio.ActionPlanError("The portfolio action plan could not be verified.")
    try:
        return parse_and_build(correction_result.completion)
    except review_portfolio.ActionPlanError as exc:
        logger.warning("Review Portfolio rejected the corrected action plan: %s", exc)
        raise


@router.get("/review-portfolio", response_class=HTMLResponse)
async def review_portfolio_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    try:
        markets = portfolio_market_data.configured_markets(config.app_settings.primary_markets)
    except portfolio_market_data.MarketConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cached_review, cached_rebalance, cached_action = await asyncio.gather(
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
        analysis_cache.get_cached_payload(
            user,
            feature=_ACTION_CACHE_FEATURE,
            input_fields=_CACHE_INPUT_FIELDS,
            payload_validator=review_portfolio.is_valid_action_plan_cache_payload,
        ),
    )
    if cached_rebalance is not None and (
        cached_review is None or not _matching_cached_inputs(cached_review, cached_rebalance)
    ):
        cached_rebalance = None
    if cached_action is not None and (
        cached_review is None
        or not _matching_cached_inputs(cached_review, cached_action)
        or not _cache_is_at_least_as_new(cached_action, cached_review)
    ):
        cached_action = None
    if cached_action is not None:
        action_payload = cached_action.get("payload")
        action_basis = action_payload.get("basis") if isinstance(action_payload, dict) else None
        if action_basis == "rebalance" and (
            cached_rebalance is None
            or not _matching_cached_inputs(cached_rebalance, cached_action)
            or not _cache_is_at_least_as_new(cached_action, cached_rebalance)
        ):
            cached_action = None
        elif action_basis == "review_only" and cached_rebalance is not None:
            cached_action = None

    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {
            "user": user,
            "cached_review": cached_review,
            "cached_rebalance": cached_rebalance,
            "cached_action": cached_action,
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

        total_steps = 12 if body.include_rebalance else 8
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

        async def generate_and_cache_action_plan(
            candidates: list[review_portfolio_schemas.ReviewActionCandidate],
            *,
            basis: review_portfolio_schemas.ActionPlanBasis,
            market_data_at: datetime.datetime | None = None,
        ) -> review_portfolio_schemas.ReviewActionPlanPayload:
            action_payload = await _generate_action_plan(
                body,
                market,
                settings,
                budget,
                snapshot,
                review_report,
                candidates,
                basis=basis,
                market_data_at=market_data_at,
            )
            await analysis_cache.set_cached_payload(
                user,
                feature=_ACTION_CACHE_FEATURE,
                inputs=cache_inputs,
                payload=action_payload.model_dump(mode="json"),
                ttl_seconds=review_portfolio.ACTION_PLAN_CACHE_TTL_SECONDS,
            )
            return action_payload

        if not body.include_rebalance:
            yield event(
                "progress",
                step=6,
                total=total_steps,
                message="Building the existing-holdings action plan...",
            )
            candidates = review_portfolio.build_review_action_candidates(
                settings,
                budget,
                snapshot,
                review_report,
            )
            try:
                action_payload = await generate_and_cache_action_plan(candidates, basis="review_only")
            except review_portfolio.ActionPlanError:
                yield event(
                    "action_error",
                    message="The portfolio action plan could not be verified. Please try again.",
                )
                yield event("complete", status="action_failed")
                return
            yield event(
                "action_result",
                html=_render_action_html(action_payload),
                action_plan=action_payload.model_dump(mode="json"),
            )
            yield event("progress", step=8, total=total_steps, message="Portfolio review complete!")
            yield event("complete", status="review_only")
            return

        if review_report.rebalance_assessment.need == "none":
            yield event(
                "progress",
                step=6,
                total=total_steps,
                message="Review complete; no meaningful rebalance was recommended.",
            )
            yield event(
                "rebalance_skipped",
                message="The validated review found no meaningful rebalance need, so no allocation plan was generated.",
            )
            yield event(
                "progress",
                step=10,
                total=total_steps,
                message="Building the existing-holdings action plan...",
            )
            candidates = review_portfolio.build_review_action_candidates(
                settings,
                budget,
                snapshot,
                review_report,
            )
            try:
                action_payload = await generate_and_cache_action_plan(candidates, basis="review_only")
            except review_portfolio.ActionPlanError:
                yield event(
                    "action_error",
                    message="The portfolio action plan could not be verified. Please try again.",
                )
                yield event("complete", status="action_failed")
                return
            yield event(
                "action_result",
                html=_render_action_html(action_payload),
                action_plan=action_payload.model_dump(mode="json"),
            )
            yield event("progress", step=12, total=total_steps, message="Portfolio review complete!")
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
        yield event(
            "progress",
            step=10,
            total=total_steps,
            message="Building the prioritized rebalance action plan...",
        )
        try:
            candidates = review_portfolio.build_rebalance_action_candidates(
                snapshot,
                review_report,
                rebalance_report,
                plan,
                all_quotes,
            )
            action_payload = await generate_and_cache_action_plan(
                candidates,
                basis="rebalance",
                market_data_at=plan.market_data_at,
            )
        except review_portfolio.ActionPlanError:
            yield event("action_error", message="The portfolio action plan could not be verified. Please try again.")
            yield event("complete", status="action_failed")
            return
        yield event(
            "action_result",
            html=_render_action_html(action_payload),
            action_plan=action_payload.model_dump(mode="json"),
        )
        yield event("progress", step=12, total=total_steps, message="Review and rebalance plan complete!")
        yield event("complete", status="success")

    return EventSourceResponse(event_generator())
