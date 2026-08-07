from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import portfolio_action_briefing as briefing_schemas
from app.services import portfolio_action_briefing, portfolio_market_data, portfolio_rebalance
from app.utils import ai

router = APIRouter(tags=["portfolio_action_briefing"])
TEMPLATE = "portfolio_action_briefing.html"

_PROMPT_WRITER_TEMPLATE = (
    "You are an expert investment-research prompt writer.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- Act only as a prompt writer for a premium AI model.\n"
    "- Do not perform research, analysis, recommendations, calculations, or summarization yourself.\n"
    "- Return one self-contained prompt and nothing else.\n"
    "\n"
    "## Validated request context\n"
    "- Market: {market_name} ({market_code}), currency {currency}\n"
    "- Action horizon: {horizon}\n"
    "- Risk tolerance: {risk_tolerance}\n"
    "- Focus: {focus}\n"
    "- Additional context: {additional_context}\n"
    "- Available cash: {available_cash} {currency}\n"
    "- Holdings: {holdings_json}\n"
    "- Watchlist: {watchlist_json}\n"
    "- Delayed market snapshots: {quotes_json}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt instructing the premium model to use web research and produce a concise, sourced portfolio\n"
    "action briefing. It must prioritize what changed and what deserves attention within the selected horizon,\n"
    "not repeat a full portfolio construction review. It must:\n"
    "1. Assess current company news, earnings, dividends, filings, analyst changes, price-sensitive events,\n"
    "   material sector developments, and portfolio-level risks.\n"
    "2. Return no more than 20 actions across holdings and watchlist names.\n"
    "3. Use BUY, SELL, TRIM, HOLD, or WATCH. BUY/SELL/TRIM must include sizing_pct: for BUY this is a percentage\n"
    "   of available cash; for SELL/TRIM it is a percentage of the existing position. HOLD/WATCH use null.\n"
    "4. Classify urgency, potential impact, and evidence confidence using only the enum values in the schema.\n"
    "5. Include only events relevant to the selected horizon and cite every action and event.\n"
    "6. Use current, reputable sources; distinguish confirmed dates from uncertainty in descriptions or warnings.\n"
    "7. Never claim that delayed prices are live and never calculate trade quantities or values.\n"
    "8. Return only JSON matching the supplied response schema, with no Markdown fences or extra text.\n"
    "\n"
    "The generated premium-model prompt must be self-contained and must not ask follow-up questions.\n"
    "Return ONLY that ready-to-execute prompt."
)

_REPAIR_PROMPT_TEMPLATE = (
    "Repair a structured portfolio action briefing response.\n"
    "\n"
    "## Repair constraints\n"
    "- Correct only structural, enum, sizing, source-reference, and submitted-ticker validation problems.\n"
    "- Preserve supported research claims and source URLs from the previous response.\n"
    "- Do not browse, add new research, invent sources, or include commentary.\n"
    "- Actions and ticker-specific events may use only these submitted tickers: {allowed_tickers_json}\n"
    "- BUY, SELL, and TRIM require sizing_pct from greater than 0 through 100.\n"
    "- HOLD and WATCH require sizing_pct null.\n"
    "- Every action and event source_id must match an id in sources.\n"
    "- Return only JSON matching the response schema.\n"
    "\n"
    "## Validation issues\n"
    "{validation_issues_json}\n"
    "\n"
    "## Previous response\n"
    "{previous_response_json}\n"
)


def _focus(request: briefing_schemas.BriefingRequest) -> str:
    if request.focus_preset == "custom":
        return request.focus_custom
    return request.focus_preset.replace("_", " ") or "General action review"


def _build_prompt_request(
    request: briefing_schemas.BriefingRequest,
    *,
    market: portfolio_market_data.MarketDefinition,
    holdings: tuple[portfolio_rebalance.Holding, ...],
    watchlist: tuple[str, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    available_cash: str,
) -> str:
    return _PROMPT_WRITER_TEMPLATE.format(
        market_name=market.name,
        market_code=market.code,
        currency=market.currency,
        horizon={
            "today": "Today",
            "7": "Next 7 days",
            "14": "Next 2 weeks",
            "30": "Next month",
            "90": "Next 3 months",
        }[request.horizon],
        risk_tolerance=request.risk_tolerance or "Not specified",
        focus=_focus(request),
        additional_context=request.additional_context or "None",
        available_cash=available_cash,
        holdings_json=json.dumps(
            [
                {
                    "ticker": holding.ticker,
                    "quantity": float(holding.quantity),
                    "average_cost": float(holding.average_cost) if holding.average_cost is not None else None,
                }
                for holding in holdings
            ]
        ),
        watchlist_json=json.dumps(watchlist),
        quotes_json=json.dumps(portfolio_action_briefing.quote_prompt_data(quotes)),
    )


@router.get("/portfolio-action-briefing", response_class=HTMLResponse)
async def portfolio_action_briefing_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    try:
        markets = portfolio_market_data.configured_markets(config.app_settings.primary_markets)
    except portfolio_market_data.MarketConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {
            "user": user,
            "market_options": markets,
            "market_codes": [market.code for market in markets],
            "market_currencies": {market.code: market.currency for market in markets},
            "default_market": markets[0].code,
        },
    )


@router.post("/portfolio-action-briefing/stream")
async def portfolio_action_briefing_stream(
    body: briefing_schemas.BriefingRequest,
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def event(event_type: str, **data: object) -> dict[str, str]:
            return {"data": json.dumps({"type": event_type, **data})}

        total_steps = 6
        yield event("progress", step=1, total=total_steps, message="Validating portfolio and watchlist...")
        try:
            market = portfolio_market_data.resolve_configured_market(
                body.target_market,
                config.app_settings.primary_markets,
            )
            holdings = portfolio_rebalance.parse_holdings(body.holdings, market)
            holding_tickers = {holding.ticker for holding in holdings}
            watchlist = portfolio_action_briefing.parse_watchlist(
                body.watchlist,
                market,
                holding_tickers=holding_tickers,
            )
            settings = portfolio_rebalance.parse_settings(
                available_cash=body.available_cash,
                fractional_shares=True,
                minimum_trade_amount="0",
                tax_context="unknown",
            )
        except (
            portfolio_action_briefing.BriefingError,
            portfolio_market_data.MarketConfigurationError,
            portfolio_market_data.MarketSymbolError,
            portfolio_rebalance.RebalanceInputError,
        ) as exc:
            yield event("error", message=str(exc))
            return

        yield event("progress", step=2, total=total_steps, message="Validating market data...")
        try:
            holding_quotes, watchlist_quotes = await asyncio.gather(
                portfolio_market_data.fetch_quotes(tuple(holding_tickers), market),
                portfolio_market_data.fetch_quotes(watchlist, market),
            )
        except portfolio_market_data.MarketDataError as exc:
            yield event("error", message=str(exc))
            return
        quotes = {**holding_quotes, **watchlist_quotes}

        yield event("progress", step=3, total=total_steps, message="Building the briefing research prompt...")
        prompt_client = config.ai_task_settings.get_ai_client("PORTFOLIO_ACTION_BRIEFING_BUILD_PROMPT")
        prompt_task = config.ai_task_settings.tasks.get("PORTFOLIO_ACTION_BRIEFING_BUILD_PROMPT")
        if not prompt_client or not prompt_task:
            yield event("error", message="AI task 'PORTFOLIO_ACTION_BRIEFING_BUILD_PROMPT' is not configured.")
            return
        prompt_result = await ai.execute_prompt(
            prompt_client,
            prompt_task.model,
            _build_prompt_request(
                body,
                market=market,
                holdings=holdings,
                watchlist=watchlist,
                quotes=quotes,
                available_cash=str(settings.available_cash),
            ),
            temperature=prompt_task.temperature,
            enable_web_search=False,
        )
        if not prompt_result.success:
            yield event("error", message=f"Failed to build the briefing prompt: {prompt_result.error}")
            return

        yield event("progress", step=4, total=total_steps, message="Researching current actions and catalysts...")
        analyze_client = config.ai_task_settings.get_ai_client("PORTFOLIO_ACTION_BRIEFING_ANALYZE")
        analyze_task = config.ai_task_settings.tasks.get("PORTFOLIO_ACTION_BRIEFING_ANALYZE")
        if not analyze_client or not analyze_task:
            yield event("error", message="AI task 'PORTFOLIO_ACTION_BRIEFING_ANALYZE' is not configured.")
            return
        analysis_result = await ai.execute_prompt(
            analyze_client,
            analyze_task.model,
            prompt_result.completion,
            temperature=analyze_task.temperature,
            response_json_schema=portfolio_action_briefing.research_schema(),
            schema_name="portfolio_action_briefing_research",
            enable_web_search=True,
        )
        if not analysis_result.success:
            yield event("error", message=f"Failed to research the action briefing: {analysis_result.error}")
            return

        yield event("progress", step=5, total=total_steps, message="Ranking portfolio actions...")
        allowed_tickers = holding_tickers | set(watchlist)
        try:
            research = portfolio_action_briefing.validate_research_scope(
                portfolio_action_briefing.parse_research(
                    analysis_result.completion,
                    allowed_tickers=allowed_tickers,
                    holding_tickers=holding_tickers,
                ),
                allowed_tickers=allowed_tickers,
            )
        except portfolio_action_briefing.BriefingResearchError as exc:
            yield event("progress", step=5, total=total_steps, message="Repairing the briefing response...")
            repair_result = await ai.execute_prompt(
                analyze_client,
                analyze_task.model,
                _REPAIR_PROMPT_TEMPLATE.format(
                    allowed_tickers_json=json.dumps(sorted(allowed_tickers)),
                    validation_issues_json=json.dumps(exc.validation_issues, indent=2),
                    previous_response_json=json.dumps(
                        {"previous_response": analysis_result.completion},
                        indent=2,
                    ),
                ),
                temperature=analyze_task.temperature,
                response_json_schema=portfolio_action_briefing.research_schema(),
                schema_name="portfolio_action_briefing_repair",
                enable_web_search=False,
            )
            if not repair_result.success:
                yield event("error", message=f"Failed to repair the action briefing: {repair_result.error}")
                return
            try:
                research = portfolio_action_briefing.validate_research_scope(
                    portfolio_action_briefing.parse_research(
                        repair_result.completion,
                        allowed_tickers=allowed_tickers,
                        holding_tickers=holding_tickers,
                    ),
                    allowed_tickers=allowed_tickers,
                )
            except portfolio_action_briefing.BriefingResearchError:
                yield event(
                    "error",
                    message="The AI action briefing remained invalid after one repair attempt.",
                )
                return

        result = portfolio_action_briefing.build_result(
            research,
            holdings=holdings,
            quotes=quotes,
            market=market,
            horizon=body.horizon,
            available_cash=settings.available_cash,
        )

        yield event("progress", step=6, total=total_steps, message="Action briefing complete!")
        yield event("result", payload=result.model_dump(mode="json"))

    return EventSourceResponse(event_generator())
