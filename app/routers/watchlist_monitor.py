import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.utils import ai

router = APIRouter(tags=["watchlist_monitor"])
TEMPLATE = "watchlist_monitor.html"

_PROMPT_TEMPLATE = (
    "You are an expert watchlist monitoring analyst and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to review a user's watchlist and flag which names deserve attention.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## Watchlist request\n"
    "- Tickers: {tickers}\n"
    "- Target market: {target_market}\n"
    "- Focus: {focus}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Treat the provided ticker list as a watchlist and review each name for current attention-worthy setups\n"
    "2. Use its web search capability to gather the latest news, catalysts, earnings context, and market developments\n"
    "3. Use the target market to disambiguate symbols when the same ticker may refer to different \n"
    "   companies or listings\n"
    "4. Evaluate each name on recent news and catalysts, technical context, valuation and \n"
    "   fundamentals, risk/reward, and timing\n"
    "5. Highlight which names look more attractive, which look risky or overextended, and which \n"
    "   deserve close monitoring\n"
    "6. Provide a short action plan for the next few days or weeks based on the requested focus\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "### 1. Priority watchlist names\n"
    "- Top 3-5 names that deserve attention\n"
    "- Why each name stands out right now\n"
    "- Whether the setup is improving, deteriorating, or neutral\n"
    "\n"
    "### 2. Market and company context\n"
    "- Relevant catalysts, news flow, or earnings events\n"
    "- Technical context and trend strength\n"
    "- Valuation and fundamentals where relevant\n"
    "\n"
    "### 3. Risk and opportunity assessment\n"
    "- Names that look attractive or improving\n"
    "- Names that look risky, overextended, or weakening\n"
    "- Clear reasons for any warnings\n"
    "\n"
    "### 4. Suggested next actions\n"
    "- What to watch next\n"
    "- Whether to add, trim, or monitor a position\n"
    "- What would change the view\n"
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


def _resolve_focus(focus: str) -> str:
    cleaned = (focus or "").strip()
    return cleaned or "General watchlist review"


def _build_prompt_request(*, tickers: str, target_market: str, focus: str) -> str:
    resolved_tickers = (tickers or "").strip() or "No tickers provided"
    return _PROMPT_TEMPLATE.format(
        tickers=resolved_tickers,
        target_market=_resolve_target_market(target_market),
        focus=_resolve_focus(focus),
    )


@router.get("/watchlist-monitor", response_class=HTMLResponse)
async def watchlist_monitor_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    return templating.templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/watchlist-monitor/stream")
async def watchlist_monitor_stream(
    request: Request,
    tickers: str = Query(...),
    target_market: str = Query(default=""),
    focus: str = Query(default=""),
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

        yield {"data": progress(1, total_steps, "Preparing watchlist analysis request...")}

        cleaned_tickers = (tickers or "").strip()
        if not cleaned_tickers:
            yield {"data": error("Tickers are required.")}
            return

        yield {"data": progress(2, total_steps, "Generating watchlist prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("WATCHLIST_MONITOR_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'WATCHLIST_MONITOR_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("WATCHLIST_MONITOR_BUILD_PROMPT")
        prompt_request = _build_prompt_request(
            tickers=cleaned_tickers,
            target_market=target_market,
            focus=focus,
        )
        prompt_result = await ai.execute_prompt(
            build_prompt_client, build_prompt_task.model, prompt_request, temperature=build_prompt_task.temperature
        )

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate watchlist prompt: {prompt_result.error}")}
            return

        yield {"data": progress(3, total_steps, "Analyzing watchlist with AI...")}
        analyze_client = config.ai_task_settings.get_ai_client("WATCHLIST_MONITOR_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'WATCHLIST_MONITOR_ANALYZE' is not configured.")}
            return

        analyze_task = config.ai_task_settings.tasks.get("WATCHLIST_MONITOR_ANALYZE")
        analysis_result = await ai.execute_prompt(
            analyze_client, analyze_task.model, prompt_result.completion, temperature=analyze_task.temperature
        )

        if not analysis_result.success:
            yield {"data": error(f"Failed to analyze watchlist: {analysis_result.error}")}
            return

        yield {"data": progress(4, total_steps, "Analysis complete!")}
        yield {"data": result(analysis_result.completion)}

    return EventSourceResponse(event_generator())
