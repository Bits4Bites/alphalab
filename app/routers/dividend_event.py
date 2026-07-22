import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.services import analysis_cache
from app.utils import ai

router = APIRouter(tags=["dividend_event"])
TEMPLATE = "dividend_event.html"
_CACHE_FEATURE = "dividend-event"
_CACHE_INPUT_FIELDS = (
    "ticker",
    "dividend_amount",
    "ex_dividend_date",
    "current_price",
    "holding_period",
    "tax_bracket",
    "additional_notes",
)

_PROMPT_TEMPLATE = (
    "You are an expert financial analyst and prompt engineer specializing in dividend strategies.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to analyze a specific dividend event and recommend whether the investor should:\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the analysis yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "1. **Capture the dividend** - buy/hold before ex-dividend date to receive the payout\n"
    "2. **Post-dividend discount** - wait and buy after the ex-dividend date at a lower price\n"
    "3. **N/A** - not enough information or the event does not present a clear opportunity\n"
    "\n"
    "## Dividend event details\n"
    "{event_context}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to fetch the latest stock price, dividend history, ex-dividend date,\n"
    "   payment date, dividend yield, payout ratio, and recent price action around prior ex-dividend dates\n"
    "2. Analyze the historical price behavior around ex-dividend dates for this stock\n"
    "   (average drop vs. dividend amount, recovery time)\n"
    "3. Evaluate the current dividend yield relative to the stock's historical yield range\n"
    "4. Consider the investor's holding period, tax situation, and transaction costs\n"
    "5. Assess broader market conditions and the stock's current momentum/trend\n"
    "6. Provide a clear recommendation: Capture the Dividend, Post-Dividend Discount, or N/A\n"
    "7. Include a risk assessment and break-even analysis\n"
    "\n"
    "## The prompt must instruct the premium model to cover:\n"
    "\n"
    "### 1. Dividend event summary\n"
    "- Stock ticker, company name, sector\n"
    "- Ex-dividend date, record date, payment date\n"
    "- Dividend amount (per share), dividend yield, payout ratio\n"
    "- Dividend frequency and growth history (last 5 years)\n"
    "\n"
    "### 2. Historical ex-dividend analysis\n"
    "- Average price drop on ex-dividend date vs. dividend amount (last 4-8 events)\n"
    "- Average recovery time after the ex-dividend drop\n"
    "- Pattern consistency (does the stock reliably drop by the dividend amount or less/more?)\n"
    "- Pre-ex-dividend run-up pattern (does the stock tend to rise before the ex-date?)\n"
    "\n"
    "### 3. Current valuation context\n"
    "- Current price vs. 52-week range\n"
    "- Current yield vs. historical yield range\n"
    "- P/E ratio and comparison to sector peers\n"
    "- Recent earnings and revenue trend\n"
    "- Analyst consensus and price targets\n"
    "\n"
    "### 4. Strategy analysis\n"
    "- **Capture the Dividend**: Expected total return (dividend income minus expected price drop),\n"
    "  tax implications of dividend income, holding period required\n"
    "- **Post-Dividend Discount**: Expected discount amount, historical reliability of the discount,\n"
    "  risk of missing a price recovery, opportunity cost\n"
    "- Break-even analysis: at what price drop does capturing become unprofitable?\n"
    "\n"
    "### 5. Recommendation\n"
    "- Clear recommendation: **Capture the Dividend**, **Post-Dividend Discount**, or **N/A**\n"
    "- Confidence level (High / Medium / Low)\n"
    "- Key factors driving the recommendation\n"
    "- Specific action plan (when to buy, target price, position size consideration)\n"
    "- Risk factors and what could invalidate the recommendation\n"
    "\n"
    "### 6. Summary table\n"
    "- Side-by-side comparison of both strategies with expected outcomes\n"
    "- Net expected return for each strategy after costs and taxes\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The prompt must instruct the premium model to format the response in Markdown, "
    "and use the hyphen character (-) instead of em-dash (\u2014) throughout.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


@router.get("/dividend-event", response_class=HTMLResponse)
async def dividend_event_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
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


@router.get("/dividend-event/stream")
async def dividend_event_stream(
    request: Request,
    ticker: str = Query(...),
    dividend_amount: str = Query(default=""),
    ex_dividend_date: str = Query(default=""),
    current_price: str = Query(default=""),
    holding_period: str = Query(default=""),
    tax_bracket: str = Query(default=""),
    additional_notes: str = Query(default=""),
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    cleaned_ticker = ticker.strip().upper()
    cleaned_dividend_amount = dividend_amount.strip()
    cleaned_ex_dividend_date = ex_dividend_date.strip()
    cleaned_current_price = current_price.strip()
    cleaned_holding_period = holding_period.strip()
    cleaned_tax_bracket = tax_bracket.strip()
    cleaned_additional_notes = additional_notes.strip()

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
        if not cleaned_ticker:
            yield {"data": error("Stock ticker is required.")}
            return

        # Step 2: Generate analysis prompt
        yield {"data": progress(2, total_steps, "Generating dividend analysis prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("DIVIDEND_EVENT_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'DIVIDEND_EVENT_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = config.ai_task_settings.tasks.get("DIVIDEND_EVENT_BUILD_PROMPT")
        context_parts = [f"Stock Ticker: {cleaned_ticker}"]
        if cleaned_dividend_amount:
            context_parts.append(f"Dividend Amount (per share): {cleaned_dividend_amount}")
        if cleaned_ex_dividend_date:
            context_parts.append(f"Ex-Dividend Date: {cleaned_ex_dividend_date}")
        if cleaned_current_price:
            context_parts.append(f"Current Stock Price: {cleaned_current_price}")
        if cleaned_holding_period:
            context_parts.append(f"Intended Holding Period: {cleaned_holding_period}")
        if cleaned_tax_bracket:
            context_parts.append(f"Tax Bracket / Situation: {cleaned_tax_bracket}")
        if cleaned_additional_notes:
            context_parts.append(f"Additional Notes: {cleaned_additional_notes}")
        event_context = "\n".join(context_parts)

        prompt_request = _PROMPT_TEMPLATE.format(event_context=event_context)
        prompt_result = await ai.execute_prompt(
            build_prompt_client, build_prompt_task.model, prompt_request, temperature=build_prompt_task.temperature
        )

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate analysis prompt: {prompt_result.error}")}
            return

        # Step 3: Analyze dividend event with generated prompt
        yield {"data": progress(3, total_steps, "Analyzing dividend event with AI...")}
        analyze_client = config.ai_task_settings.get_ai_client("DIVIDEND_EVENT_ANALYZE")
        if not analyze_client:
            yield {"data": error("AI task 'DIVIDEND_EVENT_ANALYZE' is not configured.")}
            return

        analyze_task = config.ai_task_settings.tasks.get("DIVIDEND_EVENT_ANALYZE")
        analyze_result = await ai.execute_prompt(
            analyze_client, analyze_task.model, prompt_result.completion, temperature=analyze_task.temperature
        )

        if not analyze_result.success:
            yield {"data": error(f"Failed to analyze dividend event: {analyze_result.error}")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Analysis complete!")}
        await analysis_cache.set_cached_result(
            user,
            feature=_CACHE_FEATURE,
            inputs={
                "ticker": cleaned_ticker,
                "dividend_amount": cleaned_dividend_amount,
                "ex_dividend_date": cleaned_ex_dividend_date,
                "current_price": cleaned_current_price,
                "holding_period": cleaned_holding_period,
                "tax_bracket": cleaned_tax_bracket,
                "additional_notes": cleaned_additional_notes,
            },
            content=analyze_result.completion,
        )
        yield {"data": result(analyze_result.completion)}

    return EventSourceResponse(event_generator())
