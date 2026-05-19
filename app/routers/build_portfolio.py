import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app.config import ai_task_settings
from app.dependencies import get_current_user
from app.templating import templates
from app.utils.ai import execute_prompt

router = APIRouter(tags=["build_portfolio"])
TEMPLATE = "build_portfolio.html"


@router.get("/build-portfolio", response_class=HTMLResponse)
async def build_portfolio_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/build-portfolio/stream")
async def build_portfolio_stream(
    request: Request,
    risk_tolerance: str = Query(...),
    investment_theme: str = Query(...),
    target_market: str = Query(...),
    investment_horizon: str = Query(default=""),
    budget: str = Query(default=""),
    existing_holdings: str = Query(default=""),
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
        if not risk_tolerance or not investment_theme or not target_market:
            yield {"data": error("Risk tolerance, investment theme, and target market are required.")}
            return

        # Step 2: Generate portfolio construction prompt
        yield {"data": progress(2, total_steps, "Generating portfolio strategy prompt...")}
        build_prompt_client = ai_task_settings.get_ai_client("BUILD_PORTFOLIO_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'BUILD_PORTFOLIO_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = ai_task_settings.tasks.get("BUILD_PORTFOLIO_BUILD_PROMPT")
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

        prompt_request = (
            f"Generate a ready-to-use prompt (copy-and-paste, no placeholders) to build an investment portfolio "
            f"based on the following investor profile:\n{investor_profile}\n\n"
            f"The prompt must instruct the AI to provide:\n"
            f"- A recommended portfolio allocation strategy\n"
            f"- Specific ticker recommendations with rationale for each pick\n"
            f"- Suggested allocation percentage for each ticker\n"
            f"- Approximate unit/share counts for each ticker based on current market prices\n"
            f"- Entry strategy and timing considerations\n"
            f"- Risk management and diversification notes\n"
            f"The generated prompt should instruct the AI NOT to include any suggested follow-up questions. "
            f"Output only the prompt text, nothing else."
        )
        if existing_holdings:
            prompt_request += "- How the new recommendations complement or adjust the existing holdings\n"
        prompt_request += "Output only the prompt text, nothing else."
        prompt_result = await execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate portfolio prompt: {prompt_result.error}")}
            return

        # Step 3: Build portfolio with generated prompt
        yield {"data": progress(3, total_steps, "Building portfolio with AI...")}
        portfolio_client = ai_task_settings.get_ai_client("BUILD_PORTFOLIO_ANALYZE")
        if not portfolio_client:
            yield {"data": error("AI task 'BUILD_PORTFOLIO_ANALYZE' is not configured.")}
            return

        portfolio_task = ai_task_settings.tasks.get("BUILD_PORTFOLIO_ANALYZE")
        portfolio_result = await execute_prompt(portfolio_client, portfolio_task.model, prompt_result.completion)

        if not portfolio_result.success:
            yield {"data": error(f"Failed to build portfolio: {portfolio_result.error}")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Portfolio complete!")}
        yield {"data": result(portfolio_result.completion)}

    return EventSourceResponse(event_generator())
