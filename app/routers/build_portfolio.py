import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import build_portfolio as build_portfolio_schemas
from app.services import analysis_cache, build_portfolio, portfolio_market_data
from app.utils import ai

router = APIRouter(tags=["build_portfolio"])
TEMPLATE = "build_portfolio.html"
_REPORT_TEMPLATE = "partials/build_portfolio_report.html"
_CACHE_FEATURE = "build-portfolio"
_CACHE_INPUT_FIELDS = (
    "risk_tolerance",
    "portfolio_intent",
    "target_market",
    "investment_horizon",
    "budget",
    "allow_fractional_shares",
    "existing_holdings",
)
_PROMPT_TASK_ID = "BUILD_PORTFOLIO_BUILD_PROMPT"
_RESEARCH_TASK_ID = "BUILD_PORTFOLIO_ANALYZE"
logger = logging.getLogger(__name__)


def _render_report_html(payload: build_portfolio_schemas.BuildPortfolioPayload) -> str:
    template = templating.templates.get_template(_REPORT_TEMPLATE)
    return template.render(report=payload.model_dump(mode="json"))


@router.get("/build-portfolio", response_class=HTMLResponse)
async def build_portfolio_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    try:
        markets = portfolio_market_data.configured_markets(config.app_settings.primary_markets)
    except portfolio_market_data.MarketConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cached_result = await analysis_cache.get_cached_payload(
        user,
        feature=_CACHE_FEATURE,
        input_fields=_CACHE_INPUT_FIELDS,
        payload_validator=build_portfolio.is_valid_cache_payload,
    )
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {
            "user": user,
            "cached_result": cached_result,
            "market_options": markets,
            "market_codes": [market.code for market in markets],
            "default_market": markets[0].code,
        },
    )


@router.post("/build-portfolio/stream")
async def build_portfolio_stream(
    body: build_portfolio_schemas.BuildPortfolioRequest,
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def event(event_type: str, **data: object) -> dict[str, str]:
            return {"data": json.dumps({"type": event_type, **data})}

        total_steps = 7
        yield event("progress", step=1, total=total_steps, message="Validating portfolio inputs...")

        try:
            market = portfolio_market_data.resolve_configured_market(
                body.target_market,
                config.app_settings.primary_markets,
            )
            budget = build_portfolio.parse_budget(body.budget, market)
            holdings = build_portfolio.parse_existing_holdings(body.existing_holdings, market)
        except (
            build_portfolio.BuildPortfolioError,
            portfolio_market_data.MarketConfigurationError,
            portfolio_market_data.MarketSymbolError,
        ) as exc:
            yield event("error", message=str(exc))
            return

        prompt_client = config.ai_task_settings.get_ai_client(_PROMPT_TASK_ID)
        prompt_task = config.ai_task_settings.tasks.get(_PROMPT_TASK_ID)
        research_client = config.ai_task_settings.get_ai_client(_RESEARCH_TASK_ID)
        research_task = config.ai_task_settings.tasks.get(_RESEARCH_TASK_ID)
        if not prompt_client or not prompt_task or not research_client or not research_task:
            yield event("error", message="Build Portfolio is temporarily unavailable.")
            return

        yield event("progress", step=2, total=total_steps, message="Verifying existing holdings...")
        holding_quotes: dict[str, portfolio_market_data.MarketQuote] = {}
        if holdings:
            try:
                holding_quotes = await portfolio_market_data.fetch_quotes(
                    tuple(holding.ticker for holding in holdings),
                    market,
                )
            except portfolio_market_data.MarketDataError:
                logger.warning("Build Portfolio could not verify existing holdings")
                yield event(
                    "error", message="Existing holdings could not be verified. Check the tickers and try again."
                )
                return
        try:
            verified_holdings = build_portfolio.build_verified_holdings(holdings, holding_quotes)
        except build_portfolio.BuildPortfolioError:
            yield event("error", message="Existing holdings could not be verified. Check the tickers and try again.")
            return

        yield event("progress", step=3, total=total_steps, message="Writing an adaptive research prompt...")
        prompt_result = await ai.execute_task_prompt(
            prompt_client,
            prompt_task,
            build_portfolio.build_prompt_writer_request(
                body,
                market,
                budget,
                verified_holdings,
            ),
        )
        if not prompt_result.success:
            logger.warning("Build Portfolio prompt-writing task failed")
            yield event("error", message="The portfolio research prompt could not be prepared. Please try again.")
            return
        try:
            adaptive_prompt = build_portfolio.validate_adaptive_prompt(prompt_result.completion)
        except build_portfolio.AdaptivePromptError:
            yield event("error", message="The AI returned an invalid portfolio research prompt. Please try again.")
            return

        research_prompt = build_portfolio.build_research_prompt(
            adaptive_prompt,
            body,
            market,
            budget,
            verified_holdings,
        )
        yield event("progress", step=4, total=total_steps, message="Researching the target portfolio...")
        research_result = await ai.execute_task_prompt(
            research_client,
            research_task,
            research_prompt,
            response_json_schema=build_portfolio.response_schema(),
            schema_name="build_portfolio_research",
        )
        if not research_result.success:
            logger.warning("Build Portfolio research task failed")
            yield event("error", message="Portfolio research failed. Please try again.")
            return

        yield event("progress", step=5, total=total_steps, message="Verifying recommendations and market data...")
        correction_issue: str | None = None
        try:
            report = build_portfolio.parse_research(research_result.completion, market)
            quotes = await portfolio_market_data.fetch_quotes(
                build_portfolio.recommendation_tickers(report),
                market,
            )
        except (build_portfolio.ResearchReportError, portfolio_market_data.MarketDataError) as exc:
            correction_issue = str(exc)

        if correction_issue is not None:
            correction_result = await ai.execute_task_prompt(
                research_client,
                research_task,
                build_portfolio.build_correction_prompt(
                    research_prompt,
                    research_result.completion,
                    correction_issue,
                ),
                response_json_schema=build_portfolio.response_schema(),
                schema_name="build_portfolio_research",
            )
            if not correction_result.success:
                logger.warning("Build Portfolio correction task failed")
                yield event("error", message="Portfolio recommendations could not be verified. Please try again.")
                return
            try:
                report = build_portfolio.parse_research(correction_result.completion, market)
                quotes = await portfolio_market_data.fetch_quotes(
                    build_portfolio.recommendation_tickers(report),
                    market,
                )
            except (build_portfolio.ResearchReportError, portfolio_market_data.MarketDataError):
                logger.warning("Build Portfolio rejected the corrected research")
                yield event("error", message="Portfolio recommendations could not be verified. Please try again.")
                return

        yield event("progress", step=6, total=total_steps, message="Calculating portfolio sizing and quality checks...")
        try:
            payload = build_portfolio.build_payload(
                body,
                market,
                budget,
                report,
                quotes,
                verified_holdings,
                holding_quotes,
            )
        except build_portfolio.ResearchReportError:
            yield event("error", message="Portfolio recommendations could not be calculated. Please try again.")
            return

        await analysis_cache.set_cached_payload(
            user,
            feature=_CACHE_FEATURE,
            inputs={
                "risk_tolerance": body.risk_tolerance,
                "portfolio_intent": body.portfolio_intent,
                "target_market": market.code,
                "investment_horizon": body.investment_horizon,
                "budget": body.budget,
                "allow_fractional_shares": str(body.allow_fractional_shares).lower(),
                "existing_holdings": body.existing_holdings,
            },
            payload=payload.model_dump(mode="json"),
            ttl_seconds=build_portfolio.BUILD_PORTFOLIO_CACHE_TTL_SECONDS,
        )

        yield event("progress", step=7, total=total_steps, message="Portfolio recommendation complete!")
        yield event("result", html=_render_report_html(payload))

    return EventSourceResponse(event_generator())
