import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.services import analysis_cache
from app.utils import ai

router = APIRouter(tags=["market_outlook"])
TEMPLATE = "market_outlook.html"
_CACHE_FEATURE = "market-outlook"
_CACHE_INPUT_FIELDS = ("markets",)


_PROMPT_TEMPLATE = (
    "You are an expert macroeconomic analyst and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to research recent news and events for the specified markets and produce a short-term outlook\n"
    "for the next 1-2 weeks.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## Markets to analyze\n"
    "{markets}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to find the most important news, economic data releases,\n"
    "   central bank decisions, geopolitical events, and earnings reports from the past 7 days\n"
    "   that affect the specified markets\n"
    "2. Identify the dominant macro themes and sentiment drivers for each market\n"
    "3. Highlight upcoming scheduled events in the next 1-2 weeks (economic calendar, earnings,\n"
    "   central bank meetings, political events) that could move markets\n"
    "4. Provide a directional outlook (bullish / bearish / neutral) for each market with\n"
    "   confidence level and key catalysts\n"
    "5. Flag the top risks and potential surprises that could invalidate the outlook\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "\n"
    "### 1. Recent market recap (past 7 days)\n"
    "- Key index movements and notable sector rotations\n"
    "- Major news events that drove price action\n"
    "- Volume and volatility trends\n"
    "- Currency and commodity moves relevant to the markets\n"
    "\n"
    "### 2. Macro and economic context\n"
    "- Recent economic data releases (GDP, CPI, employment, PMI, etc.)\n"
    "- Central bank policy stance and recent commentary\n"
    "- Interest rate expectations and bond market signals\n"
    "- Global trade and geopolitical developments\n"
    "\n"
    "### 3. Upcoming catalysts (next 1-2 weeks)\n"
    "- Scheduled economic data releases with expected impact\n"
    "- Central bank meetings or speeches\n"
    "- Major earnings reports\n"
    "- Political or regulatory events\n"
    "- Options expiration or index rebalancing dates\n"
    "\n"
    "### 4. Market outlook\n"
    "For each market:\n"
    "- Directional bias: Bullish / Bearish / Neutral\n"
    "- Confidence level: High / Medium / Low\n"
    "- Key support and resistance levels for major indices\n"
    "- Most likely scenario and alternative scenarios\n"
    "- Sectors or themes expected to outperform or underperform\n"
    "\n"
    "### 5. Risks and wildcards\n"
    "- Top 3 risks that could derail the base outlook\n"
    "- Potential positive surprises\n"
    "- Cross-market contagion risks\n"
    "\n"
    "### 6. Summary\n"
    "- One-paragraph executive summary of the overall outlook\n"
    "- Actionable takeaways for investors\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (\u2014) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


@router.get("/market-outlook", response_class=HTMLResponse)
async def market_outlook_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
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


@router.get("/market-outlook/stream")
async def market_outlook_stream(
    request: Request,
    markets: str = Query(default=""),
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
        resolved_markets = markets.strip() if markets.strip() else "Global"

        # Step 1: Validate inputs
        yield {"data": progress(1, total_steps, "Preparing market outlook request...")}

        # Step 2: Generate outlook prompt
        yield {"data": progress(2, total_steps, "Generating market outlook prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("MARKET_OUTLOOK_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'MARKET_OUTLOOK_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("MARKET_OUTLOOK_BUILD_PROMPT")
        prompt_request = _PROMPT_TEMPLATE.format(markets=resolved_markets)
        prompt_result = await ai.execute_prompt(
            build_prompt_client, build_prompt_task.model, prompt_request, temperature=build_prompt_task.temperature
        )

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate outlook prompt: {prompt_result.error}")}
            return

        # Step 3: Execute outlook analysis
        yield {"data": progress(3, total_steps, "Analyzing markets with AI...")}
        analyze_client = config.ai_task_settings.get_ai_client("MARKET_OUTLOOK_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'MARKET_OUTLOOK_ANALYZE' is not configured.")}
            return

        analyze_task = config.ai_task_settings.tasks.get("MARKET_OUTLOOK_ANALYZE")
        analyze_result = await ai.execute_prompt(
            analyze_client, analyze_task.model, prompt_result.completion, temperature=analyze_task.temperature
        )

        if not analyze_result.success:
            yield {"data": error(f"Failed to analyze markets: {analyze_result.error}")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Analysis complete!")}
        await analysis_cache.set_cached_result(
            user,
            feature=_CACHE_FEATURE,
            inputs={"markets": resolved_markets},
            content=analyze_result.completion,
        )
        yield {"data": result(analyze_result.completion)}

    return EventSourceResponse(event_generator())
