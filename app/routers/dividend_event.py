import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import dividend_event as dividend_event_schemas
from app.services import analysis_cache, analyze_ticker, dividend_event
from app.utils import ai

router = APIRouter(tags=["dividend_event"])
TEMPLATE = "dividend_event.html"
_REPORT_TEMPLATE = "partials/dividend_event_report.html"
_CACHE_FEATURE = "dividend-event"
_CACHE_INPUT_FIELDS = ("ticker",)
logger = logging.getLogger(__name__)


def _render_report_html(payload: dividend_event_schemas.DividendEventPayload) -> str:
    template = templating.templates.get_template(_REPORT_TEMPLATE)
    return template.render(report=payload.model_dump(mode="json"))


@router.get("/dividend-event", response_class=HTMLResponse)
async def dividend_event_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    cached_result = await analysis_cache.get_cached_payload(
        user,
        feature=_CACHE_FEATURE,
        input_fields=_CACHE_INPUT_FIELDS,
        payload_validator=dividend_event.is_valid_cache_payload,
    )
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {"user": user, "cached_result": cached_result},
    )


@router.post("/dividend-event/stream")
async def dividend_event_stream(
    request: Request,
    body: dividend_event_schemas.DividendEventRequest,
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def event(event_type: str, **data: object) -> dict[str, str]:
            return {"data": json.dumps({"type": event_type, **data})}

        total_steps = 5
        yield event("progress", step=1, total=total_steps, message="Validating the dividend event...")
        if await request.is_disconnected():
            return
        try:
            dividend_event.validate_request(body)
            asset = await analyze_ticker.fetch_asset_snapshot(body.ticker)
            dividend_event.validate_asset(asset)
        except (dividend_event.DividendEventInputError, analyze_ticker.TickerInputError) as exc:
            yield event("error", message=str(exc))
            return
        except analyze_ticker.TickerMarketDataError:
            logger.warning("Dividend Event could not resolve ticker market data")
            yield event("error", message="Ticker market data is temporarily unavailable. Please try again.")
            return

        if await request.is_disconnected():
            return

        yield event("progress", step=2, total=total_steps, message="Calculating historical dividend behavior...")
        try:
            market = await dividend_event.fetch_market_snapshot(asset, body)
        except dividend_event.DividendEventMarketDataError:
            logger.warning("Dividend Event history retrieval failed for %s", asset.yahoo_symbol)
            yield event("error", message="Dividend history is temporarily unavailable. Please try again.")
            return

        if await request.is_disconnected():
            return

        task_id = "DIVIDEND_EVENT_ANALYZE"
        analyze_client = config.ai_task_settings.get_ai_client(task_id)
        analyze_task = config.ai_task_settings.tasks.get(task_id)
        if not analyze_client or not analyze_task:
            yield event("error", message="Dividend Event analysis is temporarily unavailable.")
            return

        yield event("progress", step=3, total=total_steps, message="Researching the dividend event...")
        analysis_result = await ai.execute_task_prompt(
            analyze_client,
            analyze_task,
            dividend_event.build_research_prompt(body, asset, market),
            response_json_schema=dividend_event.response_schema(),
            schema_name="dividend_event_report",
        )
        if not analysis_result.success:
            logger.warning("Dividend Event AI task failed")
            yield event("error", message="Dividend Event research failed. Please try again.")
            return

        if await request.is_disconnected():
            return

        yield event("progress", step=4, total=total_steps, message="Validating dividend research...")
        try:
            report = dividend_event.parse_report(
                analysis_result.completion,
                request=body,
                asset=asset,
            )
        except dividend_event.DividendEventReportError as exc:
            logger.warning("Dividend Event rejected the report: %s", exc)
            yield event("error", message="The AI returned an invalid dividend report. Please try again.")
            return

        if await request.is_disconnected():
            return

        payload = dividend_event.build_payload(asset, market, report)
        await analysis_cache.set_cached_payload(
            user,
            feature=_CACHE_FEATURE,
            inputs={"ticker": asset.requested_ticker},
            payload=payload.model_dump(mode="json"),
            ttl_seconds=dividend_event.DIVIDEND_EVENT_CACHE_TTL_SECONDS,
        )

        yield event("progress", step=5, total=total_steps, message="Dividend Event analysis complete!")
        yield event(
            "result",
            html=_render_report_html(payload),
            ticker=asset.requested_ticker,
        )

    return EventSourceResponse(event_generator())
