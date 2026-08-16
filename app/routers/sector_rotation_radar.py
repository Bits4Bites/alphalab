import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.services import analysis_cache
from app.utils import ai

router = APIRouter(tags=["sector_rotation_radar"])
TEMPLATE = "sector_rotation_radar.html"
_CACHE_FEATURE = "sector-rotation-radar"
_CACHE_INPUT_FIELDS = ("target_market", "sectors", "timeframe", "bias")

_PROMPT_TEMPLATE = (
    "You are an expert sector rotation analyst and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to analyze sector rotation and identify which sectors are gaining or losing momentum.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## Sector rotation request\n"
    "- Target market: {target_market}\n"
    "- Sectors: {sectors}\n"
    "- Timeframe(s): {timeframes}\n"
    "- Bias: {bias}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to gather the latest sector-relative performance data, macro drivers,\n"
    "   and recent market leadership and laggard patterns for the target market\n"
    "2. Evaluate how sectors are rotating across the requested time horizon, including momentum, breadth,\n"
    "   and leadership changes\n"
    "3. Connect the rotation to macro drivers such as interest rates, inflation, growth expectations,\n"
    "   earnings revisions, liquidity, and sentiment\n"
    "4. Rank the sectors from most favorable to least favorable for the requested horizon\n"
    "5. Highlight which sectors to favor, which to avoid, and where the best risk/reward setup appears\n"
    "6. Provide concise trade or positioning ideas that match the requested horizon and bias\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "\n"
    "### 1. Current rotation snapshot\n"
    "- Relative strength and momentum for the requested sectors\n"
    "- Recent leadership changes and market breadth indicators\n"
    "- Whether the market is rotating into growth, defensive, cyclical, or value themes\n"
    "\n"
    "### 2. Macro context\n"
    "- Rates, inflation, economic growth, central bank tone, liquidity, and earnings trends\n"
    "- How these drivers could support or weaken current sector leadership\n"
    "\n"
    "### 3. Horizon-specific outlook\n"
    "- Best-positioned sectors for each requested timeframe\n"
    "- Sectors that look vulnerable or overextended\n"
    "- Risks that could disrupt the rotation thesis\n"
    "\n"
    "### 4. Actionable conclusion\n"
    "- Ranked list of sectors to favor or avoid\n"
    "- Short positioning ideas or trade setups\n"
    "- Clear rationale for each recommendation\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (—) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


def _resolve_timeframes(timeframe: str) -> str:
    cleaned = (timeframe or "").strip()
    if cleaned:
        return cleaned
    return "next 1-2 weeks; next 1 month; next 3 months"


def _build_prompt_request(*, target_market: str, sectors: str, timeframe: str, bias: str) -> str:
    resolved_target_market = (target_market or "").strip()
    resolved_sectors = (sectors or "").strip() or "All major sectors"
    resolved_bias = (bias or "").strip() or "No explicit bias"
    return _PROMPT_TEMPLATE.format(
        target_market=resolved_target_market,
        sectors=resolved_sectors,
        timeframes=_resolve_timeframes(timeframe),
        bias=resolved_bias,
    )


@router.get("/sector-rotation-radar", response_class=HTMLResponse)
async def sector_rotation_radar_page(
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


@router.get("/sector-rotation-radar/stream")
async def sector_rotation_radar_stream(
    request: Request,
    target_market: str = Query(...),
    sectors: str = Query(default=""),
    timeframe: str = Query(default=""),
    bias: str = Query(default=""),
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

        yield {"data": progress(1, total_steps, "Preparing sector rotation request...")}

        cleaned_target_market = (target_market or "").strip()
        if not cleaned_target_market:
            yield {"data": error("Target market is required.")}
            return

        yield {"data": progress(2, total_steps, "Generating sector rotation prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("SECTOR_ROTATION_RADAR_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'SECTOR_ROTATION_RADAR_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("SECTOR_ROTATION_RADAR_BUILD_PROMPT")
        prompt_request = _build_prompt_request(
            target_market=cleaned_target_market,
            sectors=sectors,
            timeframe=timeframe,
            bias=bias,
        )
        prompt_result = await ai.execute_task_prompt(build_prompt_client, build_prompt_task, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate sector rotation prompt: {prompt_result.error}")}
            return

        yield {"data": progress(3, total_steps, "Analyzing sector rotation with AI...")}
        analyze_client = config.ai_task_settings.get_ai_client("SECTOR_ROTATION_RADAR_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'SECTOR_ROTATION_RADAR_ANALYZE' is not configured.")}
            return

        analyze_task = config.ai_task_settings.tasks.get("SECTOR_ROTATION_RADAR_ANALYZE")
        analysis_result = await ai.execute_task_prompt(analyze_client, analyze_task, prompt_result.completion)

        if not analysis_result.success:
            yield {"data": error(f"Failed to analyze sector rotation: {analysis_result.error}")}
            return

        yield {"data": progress(4, total_steps, "Analysis complete!")}
        await analysis_cache.set_cached_result(
            user,
            feature=_CACHE_FEATURE,
            inputs={
                "target_market": cleaned_target_market,
                "sectors": (sectors or "").strip(),
                "timeframe": (timeframe or "").strip(),
                "bias": (bias or "").strip(),
            },
            content=analysis_result.completion,
        )
        yield {"data": result(analysis_result.completion)}

    return EventSourceResponse(event_generator())
