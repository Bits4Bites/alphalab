import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import analyze_ticker as analyze_ticker_schemas
from app.services import analysis_cache, analyze_ticker
from app.utils import ai

router = APIRouter(tags=["analyze_ticker"])
TEMPLATE = "analyze_ticker.html"
_REPORT_TEMPLATE = "partials/analyze_ticker_report.html"
_CACHE_FEATURE = "analyze-ticker"
_CACHE_INPUT_FIELDS = ("ticker", "quick_mode", "intent", "scenario")
logger = logging.getLogger(__name__)


def _render_report_html(payload: analyze_ticker_schemas.AnalyzeTickerPayload) -> str:
    template = templating.templates.get_template(_REPORT_TEMPLATE)
    return template.render(report=payload.model_dump(mode="json"))


@router.get("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    cached_result = await analysis_cache.get_cached_payload(
        user,
        feature=_CACHE_FEATURE,
        input_fields=_CACHE_INPUT_FIELDS,
        payload_validator=analyze_ticker.is_valid_cache_payload,
    )
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {"user": user, "cached_result": cached_result},
    )


@router.post("/analyze-ticker/stream")
async def analyze_ticker_stream(
    request: Request,
    body: analyze_ticker_schemas.AnalyzeTickerRequest,
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def event(event_type: str, **data: object) -> dict[str, str]:
            return {"data": json.dumps({"type": event_type, **data})}

        total_steps = 4
        yield event("progress", step=1, total=total_steps, message="Validating ticker identity...")
        if await request.is_disconnected():
            return
        try:
            asset = await analyze_ticker.fetch_asset_snapshot(body.ticker)
        except (analyze_ticker.TickerInputError, analyze_ticker.TickerMarketDataError) as exc:
            yield event("error", message=str(exc))
            return

        if await request.is_disconnected():
            return

        task_id = "ANALYZE_TICKER_ANALYZE_QUICK" if body.quick_mode else "ANALYZE_TICKER_ANALYZE"
        analyze_client = config.ai_task_settings.get_ai_client(task_id)
        analyze_task = config.ai_task_settings.tasks.get(task_id)
        if not analyze_client or not analyze_task:
            yield event("error", message="Ticker analysis is temporarily unavailable.")
            return

        yield event("progress", step=2, total=total_steps, message="Researching current ticker evidence...")
        analysis_result = await ai.execute_task_prompt(
            analyze_client,
            analyze_task,
            analyze_ticker.build_research_prompt(body, asset),
            response_json_schema=analyze_ticker.response_schema(),
            schema_name="analyze_ticker_research",
        )
        if not analysis_result.success:
            logger.warning("Analyze Ticker task failed: %s", task_id)
            yield event("error", message="Ticker research failed. Please try again.")
            return

        if await request.is_disconnected():
            return

        yield event("progress", step=3, total=total_steps, message="Validating ticker research...")
        try:
            research = analyze_ticker.parse_research(
                analysis_result.completion,
                request=body,
                asset=asset,
            )
        except analyze_ticker.TickerResearchError as exc:
            logger.warning(
                "Analyze Ticker rejected the %s report: %s",
                "quick" if body.quick_mode else "full",
                exc,
            )
            yield event("error", message="The AI returned an invalid ticker report. Please try again.")
            return

        if await request.is_disconnected():
            return

        payload = analyze_ticker.build_payload(asset, research)
        await analysis_cache.set_cached_payload(
            user,
            feature=_CACHE_FEATURE,
            inputs={
                "ticker": asset.requested_ticker,
                "quick_mode": str(body.quick_mode).lower(),
                "intent": body.intent,
                "scenario": body.scenario,
            },
            payload=payload.model_dump(mode="json"),
            ttl_seconds=analyze_ticker.ANALYZE_TICKER_CACHE_TTL_SECONDS,
        )

        yield event("progress", step=4, total=total_steps, message="Ticker analysis complete!")
        yield event(
            "result",
            html=_render_report_html(payload),
            ticker=asset.requested_ticker,
        )

    return EventSourceResponse(event_generator())
