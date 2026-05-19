import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from app.config import ai_task_settings
from app.dependencies import get_current_user
from app.utils.ai import execute_prompt

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")
TEMPLATE = "dashboard.html"

MAX_INTENT_LENGTH = 300


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/dashboard/stream")
async def dashboard_stream(
    request: Request,
    intent: str = Query(...),
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

        # Step 1: Validate intent
        yield {"data": progress(1, total_steps, "Validating your request...")}
        user_intent = intent.strip()
        if not user_intent:
            yield {"data": error("Please enter your intent.")}
            return
        if len(user_intent) > MAX_INTENT_LENGTH:
            yield {"data": error(f"Intent is too long. Please keep it under {MAX_INTENT_LENGTH} characters.")}
            return

        # Step 2: Generate prompt
        yield {"data": progress(2, total_steps, "Building analysis prompt...")}
        build_prompt_client = ai_task_settings.get_ai_client("DASHBOARD_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'DASHBOARD_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = ai_task_settings.tasks.get("DASHBOARD_BUILD_PROMPT")
        prompt_request = (
            f"You are a stock market research assistant. A user has submitted the following intent:\n"
            f'"{user_intent}"\n\n'
            f"First, determine if this intent is related to the stock market, investing, or financial analysis. "
            f"If it is NOT related, respond with exactly: REJECTED: <reason>\n\n"
            f"If it IS related, generate a ready-to-use prompt (copy-and-paste, no placeholders) that will "
            f"produce a concise one-page response fulfilling the user's intent. "
            f"The generated prompt should instruct the AI to provide actionable insights with data, "
            f"but NOT to include any suggested follow-up questions. "
            f"Output only the prompt text, nothing else."
        )
        prompt_result = await execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to process your request: {prompt_result.error}")}
            return

        # Check if rejected
        completion = prompt_result.completion.strip()
        if completion.upper().startswith("REJECTED:"):
            reason = completion[9:].strip()
            yield {"data": error(f"Your request doesn't appear to be related to the stock market. {reason}")}
            return

        # Step 3: Execute the prompt
        yield {"data": progress(3, total_steps, "Generating analysis...")}
        analyze_client = ai_task_settings.get_ai_client("DASHBOARD_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'DASHBOARD_ANALYZE' is not configured.")}
            return

        analyze_task = ai_task_settings.tasks.get("DASHBOARD_ANALYZE")
        analyze_result = await execute_prompt(analyze_client, analyze_task.model, completion)

        if not analyze_result.success:
            yield {"data": error(f"Failed to generate analysis: {analyze_result.error}")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Done!")}
        yield {"data": result(analyze_result.completion)}

    return EventSourceResponse(event_generator())
