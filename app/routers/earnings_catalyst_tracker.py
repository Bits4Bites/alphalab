import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.services import analysis_cache
from app.utils import ai

router = APIRouter(tags=["earnings_catalyst_tracker"])
TEMPLATE = "earnings_catalyst_tracker.html"
_CACHE_FEATURE = "earnings-catalyst-tracker"
_CACHE_INPUT_FIELDS = ("tickers", "target_market", "event_focus")

_PROMPT_TEMPLATE = (
    "You are an expert earnings catalyst analyst and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to track upcoming earnings and event catalysts that can move the provided stocks quickly.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## Earnings catalyst request\n"
    "- Tickers: {tickers}\n"
    "- Target market: {target_market}\n"
    "- Event focus: {event_focus}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Treat the provided ticker list as earnings catalyst candidates and focus on events that can\n"
    "   move each stock quickly, especially earnings reports\n"
    "2. Use its web search capability to gather upcoming earnings dates, recent earnings momentum,\n"
    "   guidance trends, and analyst expectations\n"
    "3. Use the target market to disambiguate symbols when the same ticker may refer to different\n"
    "   companies or listings\n"
    "4. Assess potential market reaction scenarios for each upcoming catalyst\n"
    "5. Highlight which names have the biggest upcoming catalyst and which may surprise positively\n"
    "   or negatively\n"
    "6. Provide a concise, actionable watch list of what to monitor in each report\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "### 1. Upcoming catalysts\n"
    "- Next earnings dates or key events for each name\n"
    "- Which names have the biggest or nearest catalyst\n"
    "- Relevant context heading into the event\n"
    "\n"
    "### 2. Earnings momentum and expectations\n"
    "- Recent earnings momentum and beat/miss history\n"
    "- Guidance trends and analyst expectations\n"
    "- Where expectations look stretched or conservative\n"
    "\n"
    "### 3. Surprise and reaction scenarios\n"
    "- Which names may surprise positively or negatively\n"
    "- Potential market reaction scenarios\n"
    "- Key numbers or commentary to watch in the report\n"
    "\n"
    "### 4. Risk/reward framing\n"
    "- Short risk/reward framing for each high-priority name\n"
    "- What would confirm or break the thesis\n"
    "- Clear, actionable takeaways\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, \n"
    "and use the hyphen character (-) instead of em-dash (—) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


def _resolve_target_market(target_market: str) -> str:
    cleaned = (target_market or "").strip()
    return cleaned or "US"


def _resolve_event_focus(event_focus: str) -> str:
    cleaned = (event_focus or "").strip()
    return cleaned or "All upcoming earnings catalysts"


def _build_prompt_request(*, tickers: str, target_market: str, event_focus: str) -> str:
    resolved_tickers = (tickers or "").strip() or "No tickers provided"
    return _PROMPT_TEMPLATE.format(
        tickers=resolved_tickers,
        target_market=_resolve_target_market(target_market),
        event_focus=_resolve_event_focus(event_focus),
    )


@router.get("/earnings-catalyst-tracker", response_class=HTMLResponse)
async def earnings_catalyst_tracker_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    cached_result = await analysis_cache.get_cached_result(
        user,
        feature=_CACHE_FEATURE,
        input_fields=_CACHE_INPUT_FIELDS,
    )
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {"user": user, "cached_result": cached_result},
    )


@router.get("/earnings-catalyst-tracker/stream")
async def earnings_catalyst_tracker_stream(
    request: Request,
    tickers: str = Query(...),
    target_market: str = Query(default=""),
    event_focus: str = Query(default=""),
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    cleaned_tickers = (tickers or "").strip()
    cleaned_target_market = (target_market or "").strip()
    cleaned_event_focus = (event_focus or "").strip()

    async def event_generator():
        def progress(step: int, total: int, message: str):
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str):
            return json.dumps({"type": "error", "message": message})

        def result(content: str):
            return json.dumps({"type": "result", "content": content})

        total_steps = 4

        yield {"data": progress(1, total_steps, "Preparing earnings catalyst request...")}

        if not cleaned_tickers:
            yield {"data": error("Tickers are required.")}
            return

        yield {"data": progress(2, total_steps, "Generating earnings catalyst prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("EARNINGS_CATALYST_TRACKER_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'EARNINGS_CATALYST_TRACKER_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("EARNINGS_CATALYST_TRACKER_BUILD_PROMPT")
        prompt_request = _build_prompt_request(
            tickers=cleaned_tickers,
            target_market=cleaned_target_market,
            event_focus=cleaned_event_focus,
        )
        prompt_result = await ai.execute_task_prompt(build_prompt_client, build_prompt_task, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate earnings catalyst prompt: {prompt_result.error}")}
            return

        yield {"data": progress(3, total_steps, "Analyzing earnings catalysts with AI...")}
        analyze_client = config.ai_task_settings.get_ai_client("EARNINGS_CATALYST_TRACKER_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'EARNINGS_CATALYST_TRACKER_ANALYZE' is not configured.")}
            return

        analyze_task = config.ai_task_settings.tasks.get("EARNINGS_CATALYST_TRACKER_ANALYZE")
        analysis_result = await ai.execute_task_prompt(analyze_client, analyze_task, prompt_result.completion)

        if not analysis_result.success:
            yield {"data": error(f"Failed to analyze earnings catalysts: {analysis_result.error}")}
            return

        yield {"data": progress(4, total_steps, "Analysis complete!")}
        await analysis_cache.set_cached_result(
            user,
            feature=_CACHE_FEATURE,
            inputs={
                "tickers": cleaned_tickers,
                "target_market": cleaned_target_market,
                "event_focus": cleaned_event_focus,
            },
            content=analysis_result.completion,
        )
        yield {"data": result(analysis_result.completion)}

    return EventSourceResponse(event_generator())
