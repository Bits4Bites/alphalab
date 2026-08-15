import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.services import analysis_cache, portfolio_market_data, portfolio_rebalance
from app.utils import ai

router = APIRouter(tags=["review_portfolio"])
TEMPLATE = "review_portfolio.html"
_CACHE_FEATURE = "review-portfolio"
_REBALANCE_CACHE_FEATURE = "review-portfolio-rebalance"
_CACHE_INPUT_FIELDS = (
    "holdings",
    "risk_tolerance",
    "investment_goals",
    "target_market",
    "investment_horizon",
    "scenario",
)
_REBALANCE_CACHE_INPUT_FIELDS = (
    *_CACHE_INPUT_FIELDS,
    "available_cash",
    "allow_fractional_shares",
    "minimum_trade_amount",
    "tax_context",
)

_PROMPT_TEMPLATE = (
    "You are an expert financial advisor and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to review an investor's existing stock portfolio and suggest concrete improvements.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## Investor profile and goal\n"
    "{investor_context}\n"
    "\n"
    "## Scenario stress test\n"
    "{scenario_context}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Adapt the portfolio review prompt to both the investor's profile and the specific holdings above:\n"
    "- For concentrated portfolios (any single position > 2x equal weight): flag over-concentration risk explicitly\n"
    "- For portfolios with poor diversification: instruct the premium model to assess sector and industry gaps\n"
    "- For conservative profiles holding high-volatility positions: flag profile-to-holding mismatches\n"
    "- For aggressive profiles holding mostly cash or bonds: flag under-deployment of risk capacity\n"
    "- For portfolios with available cash: instruct the premium model to suggest deployment opportunities\n"
    "- For ESG exclusions: screen current holdings and new suggestions against excluded sectors\n"
    "- Keep every suggested security within the required Target Market and use only its stated currency\n"
    "- Clearly label estimated trade quantities, values, costs, and cash deployment as approximate\n"
    "\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to fetch current prices, valuations, recent news, and analyst views\n"
    "   for securities listed in the required Target Market\n"
    "2. If a scenario is provided, explicitly stress-test the portfolio under that scenario\n"
    "   and note which holdings are most resilient or vulnerable\n"
    "3. Assess each existing position individually and make a clear hold / trim / exit recommendation\n"
    "4. Identify gaps and suggest specific new tickers listed in the required Target Market to fill them\n"
    "5. Propose a revised portfolio with concrete allocations — specific tickers and percentages\n"
    "6. Justify every recommendation with data (valuation, fundamentals, portfolio fit)\n"
    "7. Discuss relevant tax implications qualitatively without estimating tax liability\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "\n"
    "### 1. Portfolio health check\n"
    "- Overall diversification assessment within the target market (sector, industry, market cap, asset type)\n"
    "- Concentration risks (any over-weight positions)\n"
    "- Profile alignment check (do current holdings match the investor's stated risk tolerance and goal?)\n"
    "- Current income yield vs. goal (if passive income is relevant)\n"
    "- Tax-lot awareness where average costs are supplied, without calculating exact tax liability\n"
    "\n"
    "### 2. Position-by-position review\n"
    "For each ticker in portfolio, the premium model must assess:\n"
    "- Current fundamental health (recent earnings, revenue trend, valuation vs. peers)\n"
    "- Recent news and sentiment (last 30 days)\n"
    "- Analyst consensus and price target\n"
    "- Role and fit within the portfolio\n"
    "- Clear recommendation: HOLD / TRIM / EXIT with rationale and suggested new allocation %\n"
    "\n"
    "### 3. Portfolio gaps and new additions\n"
    "- Identify missing sectors, industries, or asset types given the investor's goal and risk profile\n"
    "- Suggest 2–5 specific new tickers to add, each with:\n"
    "  - Ticker and full name\n"
    "  - Suggested allocation %, estimated number of shares, and approximate cost\n"
    "  - Rationale (why this pick, why now, how it improves the portfolio)\n"
    "  - Key risks specific to this position\n"
    "\n"
    "### 4. Revised portfolio proposal\n"
    "- A summary table (in Markdown) of the revised portfolio listing every position, with at minimum\n"
    "  these columns: ticker, approximate allocation %, approximate number of shares, approximate cost,\n"
    "  recommendation, and the ticker's role in the portfolio (e.g. Yield Booster, Defensive, Growth, Core, Hedge)\n"
    "- Full revised holdings list: existing positions (with adjusted allocations) + new additions\n"
    "- Side-by-side comparison: current allocation % vs. proposed allocation %\n"
    "- How to get from current to proposed (what to sell, what to buy, estimated amounts, and in what order)\n"
    "- If there is available cash: how to deploy it within the revised plan\n"
    "\n"
    "### 5. Tax and execution considerations\n"
    "- Potential tax considerations of recommended exits (capital gains, wash-sale rules, franking credit loss)\n"
    "- Suggested order of execution to minimise tax impact\n"
    "- Rebalancing frequency recommendation going forward\n"
    "\n"
    "### 6. Summary\n"
    "- Top 3 most urgent actions the investor should take\n"
    '- Overall portfolio score or assessment (e.g. "well-diversified but overweight tech,\n'
    '  misaligned with conservative risk profile")\n'
    '- Suggested next review date or trigger conditions (e.g. "review if any position moves > 15%")\n'
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (\u2014) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)

_REBALANCE_REVIEW_PROMPT_TEMPLATE = (
    _PROMPT_TEMPLATE.replace(
        "- Clearly label estimated trade quantities, values, costs, and cash deployment as approximate\n",
        "- Do not calculate exact trade quantities, trade values, or resulting cash; "
        "the backend planner handles them\n",
    )
    .replace(
        "  - Suggested allocation %, estimated number of shares, and approximate cost\n",
        "  - Suggested allocation %\n",
    )
    .replace(
        "  these columns: ticker, approximate allocation %, approximate number of shares, approximate cost,\n"
        "  recommendation, and the ticker's role in the portfolio "
        "(e.g. Yield Booster, Defensive, Growth, Core, Hedge)\n",
        "  these columns: ticker, approximate allocation %, recommendation, and the ticker's role in the portfolio\n"
        "  (e.g. Yield Booster, Defensive, Growth, Core, Hedge)\n",
    )
    .replace(
        "- How to get from current to proposed (what to sell, what to buy, estimated amounts, and in what order)\n"
        "- If there is available cash: how to deploy it within the revised plan\n",
        "- High-level transition priorities without exact quantities, values, or order details\n"
        "- If there is available cash: a target allocation approach without calculating share quantities\n",
    )
    .replace(
        "- Suggested order of execution to minimise tax impact\n",
        "- General execution considerations, clearly separated from the backend-generated trade plan\n",
    )
)

_REBALANCE_PROMPT_TEMPLATE = (
    "You are a prompt writer for a premium portfolio-allocation AI model.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- Act only as a prompt writer. Do not perform research, analysis, recommendations, or calculations.\n"
    "- Return one self-contained prompt for the premium model and nothing else.\n"
    "- Do not add a preamble, explanation, commentary, or analysis.\n"
    "\n"
    "## Validated portfolio context\n"
    "- Target Market: {market_name} ({market_code})\n"
    "- Currency: {currency}\n"
    "- Risk tolerance: {risk_tolerance}\n"
    "- Investment goals: {investment_goals}\n"
    "- Investment horizon: {investment_horizon}\n"
    "- Scenario: {scenario}\n"
    "- Fractional-share trading: {fractional_shares}\n"
    "- Minimum trade amount: {minimum_trade_amount} {currency}\n"
    "- Tax context: {tax_context}\n"
    "\n"
    "Validated current portfolio snapshot:\n"
    "{snapshot_json}\n"
    "\n"
    "Existing AI portfolio review to use as research context:\n"
    "{review_content}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that instructs the premium model to design a target allocation that:\n"
    "- Uses only securities listed in {market_name}; bare market tickers are required\n"
    "- May include the special ticker CASH when a strategic cash allocation is appropriate\n"
    "- Contains no more than 20 allocations and totals exactly 100 percent\n"
    "- Gives each allocation a concise role and evidence-based rationale\n"
    "- Aligns with the profile, goals, horizon, scenario, and existing review above\n"
    "- Includes concise portfolio risks, execution guidance, and qualitative tax considerations\n"
    "- Does not calculate current weights, target values, trade quantities, trade values, or resulting cash\n"
    "- Does not estimate tax liability, fees, spreads, or slippage\n"
    "\n"
    "The premium model must return only JSON matching this schema, with no Markdown fences or extra text:\n"
    "{schema_json}\n"
    "\n"
    "Return ONLY the ready-to-execute prompt for the premium model."
)


@router.get("/review-portfolio", response_class=HTMLResponse)
async def review_portfolio_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    try:
        markets = portfolio_market_data.configured_markets(config.app_settings.primary_markets)
    except portfolio_market_data.MarketConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    cached_result, cached_rebalance = await asyncio.gather(
        analysis_cache.get_cached_result(
            user,
            feature=_CACHE_FEATURE,
            input_fields=_CACHE_INPUT_FIELDS,
        ),
        analysis_cache.get_cached_payload(
            user,
            feature=_REBALANCE_CACHE_FEATURE,
            input_fields=_REBALANCE_CACHE_INPUT_FIELDS,
            payload_validator=portfolio_rebalance.is_valid_cache_payload,
        ),
    )
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {
            "user": user,
            "cached_result": cached_result,
            "cached_rebalance": cached_rebalance,
            "market_options": markets,
            "market_codes": [market.code for market in markets],
            "market_currencies": {market.code: market.currency for market in markets},
            "default_market": markets[0].code,
        },
    )


@router.get("/review-portfolio/stream")
async def review_portfolio_stream(
    request: Request,
    holdings: Annotated[str, Query(min_length=1, max_length=6000)],
    target_market: Annotated[str, Query(min_length=1, max_length=32)],
    risk_tolerance: Annotated[str, Query(max_length=64)] = "",
    investment_goals: Annotated[str, Query(max_length=1000)] = "",
    investment_horizon: Annotated[str, Query(max_length=64)] = "",
    scenario: Annotated[str, Query(max_length=1000)] = "",
    include_rebalance: bool = False,
    available_cash: Annotated[str, Query(max_length=32)] = "0",
    allow_fractional_shares: bool = False,
    minimum_trade_amount: Annotated[str, Query(max_length=32)] = "0",
    tax_context: Annotated[str, Query(max_length=32)] = "unknown",
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    cleaned_holdings = holdings.strip()
    cleaned_risk_tolerance = risk_tolerance.strip()
    cleaned_investment_goals = investment_goals.strip()
    cleaned_target_market = target_market.strip()
    cleaned_investment_horizon = investment_horizon.strip()
    cleaned_scenario = scenario.strip()

    async def event_generator():
        def progress(step: int, total: int, message: str) -> str:
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str) -> str:
            return json.dumps({"type": "error", "message": message})

        def result(content: str) -> str:
            return json.dumps({"type": "result", "content": content})

        def review_event(content: str) -> str:
            return json.dumps({"type": "review_result", "content": content})

        def rebalance_error(message: str) -> str:
            return json.dumps({"type": "rebalance_error", "message": message})

        def complete(status: str) -> str:
            return json.dumps({"type": "complete", "status": status})

        total_steps = 9 if include_rebalance else 4

        # Step 1: Validate inputs
        yield {"data": progress(1, total_steps, "Validating inputs...")}
        if not cleaned_holdings:
            yield {"data": error("Holdings are required.")}
            return
        if not cleaned_target_market:
            yield {"data": error("Target Market is required.")}
            return

        try:
            market = portfolio_market_data.resolve_configured_market(
                cleaned_target_market,
                config.app_settings.primary_markets,
            )
        except (portfolio_market_data.MarketConfigurationError, portfolio_market_data.MarketSymbolError) as exc:
            yield {"data": error(str(exc))}
            return

        parsed_holdings: tuple[portfolio_rebalance.Holding, ...] | None = None
        rebalance_settings: portfolio_rebalance.RebalanceSettings | None = None
        if include_rebalance:
            try:
                parsed_holdings = portfolio_rebalance.parse_holdings(cleaned_holdings, market)
                rebalance_settings = portfolio_rebalance.parse_settings(
                    available_cash=available_cash,
                    fractional_shares=allow_fractional_shares,
                    minimum_trade_amount=minimum_trade_amount,
                    tax_context=tax_context,
                )
            except portfolio_rebalance.RebalanceInputError as exc:
                yield {"data": error(str(exc))}
                return

        # Step 2: Generate review prompt
        yield {"data": progress(2, total_steps, "Generating portfolio review prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("REVIEW_PORTFOLIO_BUILD_PROMPT")
        build_prompt_task = config.ai_task_settings.tasks.get("REVIEW_PORTFOLIO_BUILD_PROMPT")
        if not build_prompt_client or not build_prompt_task:
            yield {"data": error("AI task 'REVIEW_PORTFOLIO_BUILD_PROMPT' is not configured.")}
            return

        context_parts = [f"Current Holdings:\n{cleaned_holdings}"]
        if cleaned_risk_tolerance:
            context_parts.append(f"Risk Tolerance: {cleaned_risk_tolerance}")
        if cleaned_investment_goals:
            context_parts.append(f"Investment Goals: {cleaned_investment_goals}")
        context_parts.append(f"Target Market: {market.name} ({market.code})")
        context_parts.append(f"Portfolio Currency: {market.currency}")
        if cleaned_investment_horizon:
            context_parts.append(f"Investment Horizon: {cleaned_investment_horizon}")
        investor_context = "\n".join(context_parts)
        scenario_context = "- Scenario: (none provided)" if not cleaned_scenario else f"- Scenario: {cleaned_scenario}"

        review_prompt_template = _REBALANCE_REVIEW_PROMPT_TEMPLATE if include_rebalance else _PROMPT_TEMPLATE
        prompt_request = review_prompt_template.format(
            investor_context=investor_context,
            scenario_context=scenario_context,
        )
        prompt_result = await ai.execute_task_prompt(build_prompt_client, build_prompt_task, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate review prompt: {prompt_result.error}")}
            return

        # Step 3: Review portfolio with generated prompt
        yield {"data": progress(3, total_steps, "Reviewing portfolio with AI...")}
        review_client = config.ai_task_settings.get_ai_client("REVIEW_PORTFOLIO_ANALYZE")
        review_task = config.ai_task_settings.tasks.get("REVIEW_PORTFOLIO_ANALYZE")
        if not review_client or not review_task:
            yield {"data": error("AI task 'REVIEW_PORTFOLIO_ANALYZE' is not configured.")}
            return

        review_result = await ai.execute_task_prompt(review_client, review_task, prompt_result.completion)

        if not review_result.success:
            yield {"data": error(f"Failed to review portfolio: {review_result.error}")}
            return

        await analysis_cache.set_cached_result(
            user,
            feature=_CACHE_FEATURE,
            inputs={
                "holdings": cleaned_holdings,
                "risk_tolerance": cleaned_risk_tolerance,
                "investment_goals": cleaned_investment_goals,
                "target_market": market.code,
                "investment_horizon": cleaned_investment_horizon,
                "scenario": cleaned_scenario,
            },
            content=review_result.completion,
        )

        if not include_rebalance:
            # Step 4: Done
            yield {"data": progress(4, total_steps, "Review complete!")}
            yield {"data": result(review_result.completion)}
            return

        yield {"data": review_event(review_result.completion)}
        assert parsed_holdings is not None
        assert rebalance_settings is not None

        # Step 4: Retrieve current market data
        yield {"data": progress(4, total_steps, "Retrieving current market prices...")}
        try:
            quotes = await portfolio_market_data.fetch_quotes(
                tuple(holding.ticker for holding in parsed_holdings),
                market,
            )
            snapshot = portfolio_rebalance.build_snapshot(
                parsed_holdings,
                quotes,
                rebalance_settings.available_cash,
            )
        except (portfolio_market_data.MarketDataError, portfolio_rebalance.RebalanceCalculationError) as exc:
            yield {"data": rebalance_error(str(exc))}
            yield {"data": complete("rebalance_failed")}
            return

        # Step 5: Generate the premium allocation prompt
        yield {"data": progress(5, total_steps, "Generating rebalance allocation prompt...")}
        rebalance_prompt_client = config.ai_task_settings.get_ai_client("REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT")
        rebalance_prompt_task = config.ai_task_settings.tasks.get("REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT")
        if not rebalance_prompt_client or not rebalance_prompt_task:
            yield {"data": rebalance_error("AI task 'REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT' is not configured.")}
            yield {"data": complete("rebalance_failed")}
            return

        prompt_request = _REBALANCE_PROMPT_TEMPLATE.format(
            market_name=market.name,
            market_code=market.code,
            currency=market.currency,
            risk_tolerance=cleaned_risk_tolerance or "(not provided)",
            investment_goals=cleaned_investment_goals or "(not provided)",
            investment_horizon=cleaned_investment_horizon or "(not provided)",
            scenario=cleaned_scenario or "(not provided)",
            fractional_shares=("allowed" if rebalance_settings.fractional_shares else "not allowed"),
            minimum_trade_amount=format(rebalance_settings.minimum_trade_amount, "f"),
            tax_context=portfolio_rebalance.TAX_CONTEXTS[rebalance_settings.tax_context],
            snapshot_json=json.dumps(portfolio_rebalance.snapshot_prompt_data(snapshot), indent=2),
            review_content=review_result.completion,
            schema_json=json.dumps(portfolio_rebalance.recommendation_schema(), indent=2),
        )
        rebalance_prompt_result = await ai.execute_task_prompt(
            rebalance_prompt_client,
            rebalance_prompt_task,
            prompt_request,
        )
        if not rebalance_prompt_result.success:
            yield {"data": rebalance_error(f"Failed to generate rebalance prompt: {rebalance_prompt_result.error}")}
            yield {"data": complete("rebalance_failed")}
            return

        # Step 6: Generate and validate target allocations
        yield {"data": progress(6, total_steps, "Designing target allocation with AI...")}
        rebalance_client = config.ai_task_settings.get_ai_client("REVIEW_PORTFOLIO_REBALANCE_ANALYZE")
        rebalance_task = config.ai_task_settings.tasks.get("REVIEW_PORTFOLIO_REBALANCE_ANALYZE")
        if not rebalance_client or not rebalance_task:
            yield {"data": rebalance_error("AI task 'REVIEW_PORTFOLIO_REBALANCE_ANALYZE' is not configured.")}
            yield {"data": complete("rebalance_failed")}
            return

        allocation_result = await ai.execute_task_prompt(
            rebalance_client,
            rebalance_task,
            rebalance_prompt_result.completion,
            response_json_schema=portfolio_rebalance.recommendation_schema(),
            schema_name="portfolio_rebalance_target",
        )
        if not allocation_result.success:
            yield {"data": rebalance_error(f"Failed to design target allocation: {allocation_result.error}")}
            yield {"data": complete("rebalance_failed")}
            return

        try:
            recommendation = portfolio_rebalance.normalize_recommendation(
                portfolio_rebalance.parse_recommendation(allocation_result.completion),
                market,
            )
        except portfolio_rebalance.RebalanceRecommendationError as exc:
            yield {"data": rebalance_error(str(exc))}
            yield {"data": complete("rebalance_failed")}
            return

        # Step 7: Retrieve prices for any proposed additions
        yield {"data": progress(7, total_steps, "Validating proposed securities and prices...")}
        missing_tickers = tuple(
            ticker_value
            for ticker_value in portfolio_rebalance.recommended_security_tickers(recommendation)
            if ticker_value not in quotes
        )
        try:
            if missing_tickers:
                quotes.update(await portfolio_market_data.fetch_quotes(missing_tickers, market))
        except portfolio_market_data.MarketDataError as exc:
            yield {"data": rebalance_error(str(exc))}
            yield {"data": complete("rebalance_failed")}
            return

        # Step 8: Calculate deterministic trades
        yield {"data": progress(8, total_steps, "Calculating rebalance trades...")}
        try:
            plan = portfolio_rebalance.calculate_plan(
                snapshot,
                recommendation,
                quotes,
                market,
                rebalance_settings,
            )
        except portfolio_rebalance.RebalanceCalculationError as exc:
            yield {"data": rebalance_error(str(exc))}
            yield {"data": complete("rebalance_failed")}
            return

        plan_content = portfolio_rebalance.render_plan_markdown(plan)
        plan_payload = portfolio_rebalance.cache_payload(plan_content, plan)
        await analysis_cache.set_cached_payload(
            user,
            feature=_REBALANCE_CACHE_FEATURE,
            inputs={
                "holdings": cleaned_holdings,
                "risk_tolerance": cleaned_risk_tolerance,
                "investment_goals": cleaned_investment_goals,
                "target_market": market.code,
                "investment_horizon": cleaned_investment_horizon,
                "scenario": cleaned_scenario,
                "available_cash": format(rebalance_settings.available_cash, "f"),
                "allow_fractional_shares": str(rebalance_settings.fractional_shares).lower(),
                "minimum_trade_amount": format(rebalance_settings.minimum_trade_amount, "f"),
                "tax_context": rebalance_settings.tax_context,
            },
            payload=plan_payload,
        )
        yield {
            "data": json.dumps(
                {
                    "type": "rebalance_result",
                    "content": plan_content,
                    "plan": plan.model_dump(mode="json"),
                }
            )
        }

        # Step 9: Done
        yield {"data": progress(9, total_steps, "Review and rebalance plan complete!")}
        yield {"data": complete("success")}

    return EventSourceResponse(event_generator())
