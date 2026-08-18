import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import market_outlook as market_outlook_schemas
from app.services import analysis_cache, market_outlook
from app.utils import ai

router = APIRouter(tags=["market_outlook"])
TEMPLATE = "market_outlook.html"
_REPORT_TEMPLATE = "partials/market_outlook_report.html"
_CACHE_FEATURE = "market-outlook"
_CACHE_INPUT_FIELDS = ("markets",)
logger = logging.getLogger(__name__)


def _render_report_html(report: market_outlook_schemas.MarketOutlookReport) -> str:
    template = templating.templates.get_template(_REPORT_TEMPLATE)
    return template.render(report=report.model_dump(mode="json"))


@router.get("/market-outlook", response_class=HTMLResponse)
async def market_outlook_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    cached_result = await analysis_cache.get_cached_payload(
        user,
        feature=_CACHE_FEATURE,
        input_fields=_CACHE_INPUT_FIELDS,
        payload_validator=market_outlook.is_valid_cache_payload,
    )
    return templating.templates.TemplateResponse(
        request,
        TEMPLATE,
        {"user": user, "cached_result": cached_result},
    )


@router.post("/market-outlook/stream")
async def market_outlook_stream(
    request: Request,
    body: market_outlook_schemas.MarketOutlookRequest,
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def event(event_type: str, **data: object) -> dict[str, str]:
            return {"data": json.dumps({"type": event_type, **data})}

        total_steps = 3
        markets = market_outlook.resolve_markets(body.markets)
        yield event("progress", step=1, total=total_steps, message="Validating market outlook request...")

        if await request.is_disconnected():
            return

        analyze_client = config.ai_task_settings.get_ai_client("MARKET_OUTLOOK_ANALYZE")
        analyze_task = config.ai_task_settings.tasks.get("MARKET_OUTLOOK_ANALYZE")
        if not analyze_client or not analyze_task:
            yield event("error", message="Market Outlook analysis is temporarily unavailable.")
            return

        yield event("progress", step=2, total=total_steps, message="Researching current market conditions...")
        analyze_result = await ai.execute_task_prompt(
            analyze_client,
            analyze_task,
            market_outlook.build_research_prompt(markets),
            response_json_schema=market_outlook.response_schema(),
            schema_name="market_outlook_report",
        )
        if not analyze_result.success:
            logger.warning("Market Outlook analysis task failed")
            yield event("error", message="Market Outlook research failed. Please try again.")
            return

        if await request.is_disconnected():
            return

        try:
            report = market_outlook.parse_report(
                analyze_result.completion,
                expected_markets=markets,
            )
        except market_outlook.MarketOutlookReportError:
            yield event("error", message="The AI returned an invalid Market Outlook report. Please try again.")
            return

        report_payload = report.model_dump(mode="json")
        await analysis_cache.set_cached_payload(
            user,
            feature=_CACHE_FEATURE,
            inputs={"markets": ", ".join(markets)},
            payload=report_payload,
            ttl_seconds=market_outlook.MARKET_OUTLOOK_CACHE_TTL_SECONDS,
        )

        yield event("progress", step=3, total=total_steps, message="Market Outlook complete!")
        yield event("result", html=_render_report_html(report))

    return EventSourceResponse(event_generator())
