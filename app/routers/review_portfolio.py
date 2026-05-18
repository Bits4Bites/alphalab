import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from app.config import ai_task_settings
from app.dependencies import get_current_user
from app.utils.ai import execute_prompt

router = APIRouter(tags=["review_portfolio"])
templates = Jinja2Templates(directory="app/templates")
TEMPLATE = "review_portfolio.html"


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

        prompt_request = (
            f"Generate a ready-to-use prompt (copy-and-paste, no placeholders) to review and analyze "
            f"the following investment portfolio:\n{investor_context}\n\n"
            f"The prompt must instruct the AI to provide:\n"
            f"- Overall portfolio health assessment (diversification, risk exposure, sector balance)\n"
            f"- Individual holding analysis (performance outlook, strengths, concerns)\n"
            f"- Portfolio concentration risks and overlaps\n"
            f"- Rebalancing recommendations with rationale\n"
            f"- Suggested additions or removals to improve the portfolio\n"
            f"- Market conditions and timing considerations\n"
            f"The generated prompt should instruct the AI NOT to include any suggested follow-up questions. "
            f"Output only the prompt text, nothing else."
        )
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
