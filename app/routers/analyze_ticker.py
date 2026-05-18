import json

import yfinance as yf
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from app.config import ai_task_settings
from app.dependencies import get_current_user
from app.utils.ai import execute_prompt
from app.utils.ticker import to_yfinance_format

router = APIRouter(tags=["analyze_ticker"])
templates = Jinja2Templates(directory="app/templates")
TEMPLATE = "analyze_ticker.html"


@router.get("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/analyze-ticker/stream")
async def analyze_ticker_stream(
    request: Request,
    ticker: str = Query(...),
    quick_mode: bool = Query(default=False),
    user: dict = Depends(get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def progress(step: int, total: int, message: str):
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str):
            return json.dumps({"type": "error", "message": message})

        def result(content: str):
            return json.dumps({"type": "result", "content": content})

        total_steps = 5

        # Step 1: Validate ticker format
        yield {"data": progress(1, total_steps, "Validating ticker format...")}
        yf_ticker = to_yfinance_format(ticker)
        if yf_ticker is None:
            yield {"data": error(f"Unsupported exchange in ticker '{ticker}'.")}
            return

        # Step 2: Fetch ticker info
        yield {"data": progress(2, total_steps, "Fetching ticker information...")}
        info = yf.Ticker(yf_ticker).info
        if not info or info.get("trailingPegRatio") is None and info.get("shortName") is None:
            yield {"data": error(f"Ticker '{ticker}' not found or invalid.")}
            return

        if info.get("quoteType") not in ("EQUITY", "ETF"):
            yield {"data": error(f"Ticker '{ticker}' is not tradeable.")}
            return

        # Step 3: Generate analysis prompt
        yield {"data": progress(3, total_steps, "Generating analysis prompt...")}
        build_prompt_client = ai_task_settings.get_ai_client("ANALYZE_TICKER_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'ANALYZE_TICKER_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = ai_task_settings.tasks.get("ANALYZE_TICKER_BUILD_PROMPT")
        company_name = info.get("longName") or info.get("shortName")
        if quick_mode:
            prompt_request = (
                f"Generate a ready-to-use prompt (copy-and-paste, no placeholders) to analyze the stock ticker "
                f"{ticker} ({company_name}). The prompt must instruct the AI to provide a concise one-page analysis "
                f"that includes:\n"
                f"- A brief current stock quotation summary\n"
                f"- Stock outlook with trend prediction for the next 2 weeks, 1 month, and 3 months\n"
                f"- Confidence level (low/medium/high) for each outlook period\n"
                f"Output only the prompt text, nothing else."
            )
        else:
            prompt_request = (
                f"Generate a ready-to-use prompt (copy-and-paste, no placeholders) to analyze the stock ticker "
                f"{ticker} ({company_name}). The prompt must instruct the AI to provide:\n"
                f"- A detailed fundamental and technical analysis of the stock\n"
                f"- A summary of current stock quotation (price, volume, market cap, P/E, etc.)\n"
                f"- Stock outlook with trend prediction for the next 2 weeks, 1 month, and 3 months\n"
                f"- Confidence level (low/medium/high) for each outlook period\n"
                f"Output only the prompt text, nothing else."
            )
        prompt_result = await execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate analysis prompt: {prompt_result.error}")}
            return

        # ### DEBUG: START
        # yield {"data": progress(5, total_steps, "Analysis complete!")}
        # yield {"data": result(prompt_result.completion)}
        # return
        # ### DEBUG: END

        # Step 4: Analyze ticker with generated prompt
        analyze_task_id = "ANALYZE_TICKER_ANALYZE_QUICK" if quick_mode else "ANALYZE_TICKER_ANALYZE"
        yield {"data": progress(4, total_steps, "Analyzing ticker with AI...")}
        analyze_client = ai_task_settings.get_ai_client(analyze_task_id)
        if not analyze_client:
            yield {"data": error(f"AI task '{analyze_task_id}' is not configured.")}
            return

        analyze_task = ai_task_settings.tasks.get(analyze_task_id)
        analysis_result = await execute_prompt(analyze_client, analyze_task.model, prompt_result.completion)

        if not analysis_result.success:
            yield {"data": error(f"Failed to analyze ticker: {analysis_result.error}")}
            return

        # Step 5: Done
        yield {"data": progress(5, total_steps, "Analysis complete!")}
        yield {"data": result(analysis_result.completion)}

    return EventSourceResponse(event_generator())
