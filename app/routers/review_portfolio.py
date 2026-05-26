import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app.config import ai_task_settings
from app.dependencies import get_current_user
from app.templating import templates
from app.utils.ai import execute_prompt

router = APIRouter(tags=["review_portfolio"])
TEMPLATE = "review_portfolio.html"

_PROMPT_TEMPLATE = (
    "You are an expert financial advisor and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to review an investor's existing stock portfolio and suggest concrete improvements.\n"
    "\n"
    "## Investor profile and goal\n"
    "{investor_context}\n"
    "\n"
    "## Your instructions\n"
    "Adapt the portfolio review prompt to both the investor's profile and the specific holdings above:\n"
    "- For concentrated portfolios (any single position > 2x equal weight): flag over-concentration risk explicitly\n"
    "- For portfolios with poor diversification: instruct the premium model to assess sector/geography gaps\n"
    "- For conservative profiles holding high-volatility positions: flag profile-to-holding mismatches\n"
    "- For aggressive profiles holding mostly cash or bonds: flag under-deployment of risk capacity\n"
    "- For portfolios with available cash: instruct the premium model to suggest deployment opportunities\n"
    "- For ESG exclusions: screen current holdings and new suggestions against excluded sectors\n"
    "\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to fetch current prices, valuations, recent news, and analyst views\n"
    "2. Assess each existing position individually and make a clear hold / trim / exit recommendation\n"
    "3. Identify gaps in the portfolio and suggest specific new tickers to fill them\n"
    "4. Propose a revised portfolio with concrete allocations — specific tickers and percentages\n"
    "5. Justify every recommendation with data (valuation, fundamentals, portfolio fit)\n"
    "6. Account for relevant tax implications of any suggested exits\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "\n"
    "### 1. Portfolio health check\n"
    "- Overall diversification assessment (sector, geography, market cap, asset type)\n"
    "- Concentration risks (any over-weight positions)\n"
    "- Profile alignment check (do current holdings match the investor's stated risk tolerance and goal?)\n"
    "- Current income yield vs. goal (if passive income is relevant)\n"
    "- Unrealised gain/loss summary and tax lot awareness\n"
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
    "- Identify missing sectors, geographies, or asset types given the investor's goal and risk profile\n"
    "- Suggest 2–5 specific new tickers to add, each with:\n"
    "  - Ticker and full name\n"
    "  - Suggested allocation % and estimated number of shares\n"
    "  - Rationale (why this pick, why now, how it improves the portfolio)\n"
    "  - Key risks specific to this position\n"
    "\n"
    "### 4. Revised portfolio proposal\n"
    "- Full revised holdings list: existing positions (with adjusted allocations) + new additions\n"
    "- Side-by-side comparison: current allocation % vs. proposed allocation %\n"
    "- How to get from current to proposed (what to sell, what to buy, in what order)\n"
    "- If there is available cash: how to deploy it within the revised plan\n"
    "\n"
    "### 5. Tax and execution considerations\n"
    "- Relevant tax implications of recommended exits (capital gains, wash-sale rules, franking credit loss)\n"
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
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (\u2014) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


@router.get("/review-portfolio", response_class=HTMLResponse)
async def review_portfolio_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/review-portfolio/stream")
async def review_portfolio_stream(
    request: Request,
    holdings: str = Query(...),
    risk_tolerance: str = Query(default=""),
    investment_goals: str = Query(default=""),
    target_market: str = Query(default=""),
    investment_horizon: str = Query(default=""),
    user: dict = Depends(get_current_user),
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
        if not holdings.strip():
            yield {"data": error("Holdings are required.")}
            return

        # Step 2: Generate review prompt
        yield {"data": progress(2, total_steps, "Generating portfolio review prompt...")}
        build_prompt_client = ai_task_settings.get_ai_client("REVIEW_PORTFOLIO_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'REVIEW_PORTFOLIO_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = ai_task_settings.tasks.get("REVIEW_PORTFOLIO_BUILD_PROMPT")
        context_parts = [f"Current Holdings:\n{holdings.strip()}"]
        if risk_tolerance:
            context_parts.append(f"Risk Tolerance: {risk_tolerance}")
        if investment_goals:
            context_parts.append(f"Investment Goals: {investment_goals}")
        if target_market:
            context_parts.append(f"Target Market: {target_market}")
        if investment_horizon:
            context_parts.append(f"Investment Horizon: {investment_horizon}")
        investor_context = "\n".join(context_parts)

        prompt_request = _PROMPT_TEMPLATE.format(investor_context=investor_context)
        prompt_result = await execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate review prompt: {prompt_result.error}")}
            return

        # Step 3: Review portfolio with generated prompt
        yield {"data": progress(3, total_steps, "Reviewing portfolio with AI...")}
        review_client = ai_task_settings.get_ai_client("REVIEW_PORTFOLIO_ANALYZE")
        if not review_client:
            yield {"data": error("AI task 'REVIEW_PORTFOLIO_ANALYZE' is not configured.")}
            return

        review_task = ai_task_settings.tasks.get("REVIEW_PORTFOLIO_ANALYZE")
        review_result = await execute_prompt(review_client, review_task.model, prompt_result.completion)

        if not review_result.success:
            yield {"data": error(f"Failed to review portfolio: {review_result.error}")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Review complete!")}
        yield {"data": result(review_result.completion)}

    return EventSourceResponse(event_generator())
