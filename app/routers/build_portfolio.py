import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.utils import ai

router = APIRouter(tags=["build_portfolio"])
TEMPLATE = "build_portfolio.html"

_PROMPT_TEMPLATE = (
    "You are an expert financial advisor and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to help an investor build a stock portfolio from scratch.\n"
    "\n"
    "## Investor profile and goal\n"
    "{investor_profile}\n"
    "\n"
    "## Your instructions\n"
    "Adapt the portfolio-building prompt to the investor's specific profile:\n"
    "- For conservative profiles: weight toward dividend stocks, blue chips, bonds, or bond ETFs\n"
    "- For aggressive profiles: allow higher allocation to growth stocks, small-caps, thematic ETFs\n"
    "- For passive income goals: emphasize REITs, dividend ETFs, high-yield equities\n"
    "- For short horizons: reduce volatility exposure, increase cash or short-duration assets\n"
    "- For ESG exclusions: explicitly instruct the premium model to screen out excluded sectors\n"
    "\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to gather current market data, valuations, and recent performance\n"
    "2. Recommend a concrete, actionable portfolio - specific tickers, not vague asset classes\n"
    "3. Justify every pick with data (valuation, growth profile, role in the portfolio)\n"
    "4. Define the allocation clearly (percentage per position)\n"
    "5. Flag key risks for the overall portfolio and for individual positions\n"
    "6. Keep the portfolio manageable (typically 8–15 positions unless the investor profile suggests otherwise)\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "- Proposed asset allocation strategy (equities / ETFs / REITs / bonds / cash %)\n"
    "- Individual stock/ETF picks with:\n"
    "  - Ticker and full name\n"
    "  - Allocation % and estimated number of shares\n"
    "  - Rationale (why this pick, why this weighting)\n"
    "  - Key risks specific to this position\n"
    "- Portfolio-level analysis:\n"
    "  - Diversification assessment (sector, geography, market cap spread)\n"
    "  - Expected income yield (if relevant to goal)\n"
    "  - Overall risk profile vs. stated tolerance\n"
    "- Relevant tax considerations (e.g. franking credits, withholding tax, capital gains treatment)\n"
    "- Suggested rebalancing frequency\n"
    "- A clear next-steps section for the investor to act on the recommendations\n"
    "{existing_holdings_instruction}"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (\u2014) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


@router.get("/build-portfolio", response_class=HTMLResponse)
async def build_portfolio_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    return templating.templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/build-portfolio/stream")
async def build_portfolio_stream(
    request: Request,
    risk_tolerance: str = Query(...),
    investment_theme: str = Query(...),
    target_market: str = Query(...),
    investment_horizon: str = Query(default=""),
    budget: str = Query(default=""),
    existing_holdings: str = Query(default=""),
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def progress(step: int, total: int, message: str):
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str):
            return json.dumps({"type": "error", "message": message})

        def result(content: str):
            return json.dumps({"type": "result", "content": content})

        total_steps = 4

        # Step 1: Validate inputs
        yield {"data": progress(1, total_steps, "Validating inputs...")}
        if not risk_tolerance or not investment_theme or not target_market:
            yield {"data": error("Risk tolerance, investment theme, and target market are required.")}
            return

        # Step 2: Generate portfolio construction prompt
        yield {"data": progress(2, total_steps, "Generating portfolio strategy prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("BUILD_PORTFOLIO_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'BUILD_PORTFOLIO_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("BUILD_PORTFOLIO_BUILD_PROMPT")
        context_parts = [
            f"Risk Tolerance: {risk_tolerance}",
            f"Investment Theme/Flavor: {investment_theme}",
            f"Target Market: {target_market}",
        ]
        if investment_horizon:
            context_parts.append(f"Investment Horizon: {investment_horizon}")
        if budget:
            context_parts.append(f"Budget: {budget}")
        if existing_holdings:
            context_parts.append(f"Existing Holdings: {existing_holdings}")
        investor_profile = "\n".join(context_parts)

        prompt_request = _PROMPT_TEMPLATE.format(
            investor_profile=investor_profile,
            existing_holdings_instruction=(
                "- How the new recommendations complement or adjust the existing holdings\n"
                if existing_holdings
                else ""
            ),
        )
        prompt_result = await ai.execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate portfolio prompt: {prompt_result.error}")}
            return

        # ### DEBUG: START
        # yield {"data": progress(total_steps, total_steps, "Analysis complete!")}
        # yield {"data": result(prompt_result.completion)}
        # return
        # ### DEBUG: END

        # Step 3: Build portfolio with generated prompt
        yield {"data": progress(3, total_steps, "Building portfolio with AI...")}
        portfolio_client = config.ai_task_settings.get_ai_client("BUILD_PORTFOLIO_ANALYZE")
        if not portfolio_client:
            yield {"data": error("AI task 'BUILD_PORTFOLIO_ANALYZE' is not configured.")}
            return

        portfolio_task = config.ai_task_settings.tasks.get("BUILD_PORTFOLIO_ANALYZE")
        portfolio_result = await ai.execute_prompt(portfolio_client, portfolio_task.model, prompt_result.completion)

        if not portfolio_result.success:
            yield {"data": error(f"Failed to build portfolio: {portfolio_result.error}")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Portfolio complete!")}
        yield {"data": result(portfolio_result.completion)}

    return EventSourceResponse(event_generator())
