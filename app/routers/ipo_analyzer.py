import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.utils import ai

router = APIRouter(tags=["ipo_analyzer"])
TEMPLATE = "ipo_analyzer.html"

_PROMPT_TEMPLATE = (
    "You are an expert IPO analyst and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to analyze an upcoming or recently listed IPO and estimate the likely price range after the\n"
    "first day, first week, two weeks, and one month.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## IPO details\n"
    "{ipo_context}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to gather the latest company information, IPO pricing details,\n"
    "   prospectus highlights, recent news, and comparable companies\n"
    "2. Assess market demand, sector momentum, valuation, and risk factors for the IPO\n"
    "3. Estimate a realistic price range after the first day, first week, two weeks, and one month\n"
    "4. Explain the main drivers behind each estimate and identify why the stock may move higher or lower\n"
    "5. Highlight the key upside catalysts, downside risks, and any red flags for investors\n"
    "6. Provide a concise recommendation for whether the IPO looks attractive at launch\n"
    "   and for a short-term holding period\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "- Company overview, business model, sector, and market opportunity\n"
    "- IPO pricing details, valuation, and comparison to peers\n"
    "- Recent news, analyst coverage, and market sentiment\n"
    "- First-day trading outlook and likely opening price range\n"
    "- One-week, two-week, and one-month expected price ranges\n"
    "- Key catalysts, risks, and what could change the outlook\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (\u2014) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


@router.get("/analyze-ipo", response_class=HTMLResponse)
async def ipo_analyzer_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    return templating.templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/analyze-ipo/stream")
async def ipo_analyzer_stream(
    request: Request,
    company_name: str = Query(...),
    additional_notes: str = Query(default=""),
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

        yield {"data": progress(1, total_steps, "Validating inputs...")}
        if not company_name.strip():
            yield {"data": error("Company name is required.")}
            return

        yield {"data": progress(2, total_steps, "Generating IPO analysis prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("IPO_ANALYZER_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'IPO_ANALYZER_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("IPO_ANALYZER_BUILD_PROMPT")
        context_parts = [f"Company Name: {company_name.strip()}"]
        if additional_notes.strip():
            context_parts.append(f"Additional Notes: {additional_notes.strip()}")

        prompt_request = _PROMPT_TEMPLATE.format(ipo_context="\n".join(context_parts))
        prompt_result = await ai.execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate IPO analysis prompt: {prompt_result.error}")}
            return

        yield {"data": progress(3, total_steps, "Analyzing IPO with AI...")}
        analyze_client = config.ai_task_settings.get_ai_client("IPO_ANALYZER_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'IPO_ANALYZER_ANALYZE' is not configured.")}
            return

        analyze_task = config.ai_task_settings.tasks.get("IPO_ANALYZER_ANALYZE")
        analyze_result = await ai.execute_prompt(analyze_client, analyze_task.model, prompt_result.completion)

        if not analyze_result.success:
            yield {"data": error(f"Failed to analyze IPO: {analyze_result.error}")}
            return

        yield {"data": progress(4, total_steps, "IPO analysis complete!")}
        yield {"data": result(analyze_result.completion)}

    return EventSourceResponse(event_generator())
