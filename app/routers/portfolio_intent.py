import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app import config, dependencies, templating
from app.schemas import portfolio_intent as portfolio_intent_schemas
from app.services import portfolio_intent as portfolio_intent_service
from app.utils import ai

router = APIRouter(tags=["portfolio_intent"])
_TASK_ID = "DRAFT_PORTFOLIO_INTENT"
_TEMPLATE = "draft_portfolio_intent.html"
_TASK_TIMEOUT_SECONDS = 60.0
logger = logging.getLogger(__name__)


@router.get("/draft-portfolio-intent", response_class=HTMLResponse)
async def draft_portfolio_intent_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    return templating.templates.TemplateResponse(request, _TEMPLATE, {"user": user})


@router.post("/portfolio-intent/draft", response_model=portfolio_intent_schemas.DraftIntentResponse)
async def draft_portfolio_intent(
    request: Request,
    body: portfolio_intent_schemas.DraftIntentRequest,
    _user: dict = Depends(dependencies.get_current_user),
) -> portfolio_intent_schemas.DraftIntentResponse:
    deterministic_response = portfolio_intent_service.deterministic_clarification(body)
    if deterministic_response is not None:
        return deterministic_response

    if await request.is_disconnected():
        raise HTTPException(status_code=499, detail="Portfolio Intent request was cancelled.")

    task_config = config.ai_task_settings.tasks.get(_TASK_ID)
    client = config.ai_task_settings.get_ai_client(_TASK_ID)
    if task_config is None or client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Portfolio Intent drafting is temporarily unavailable.",
        )

    try:
        result = await asyncio.wait_for(
            ai.execute_task_prompt(
                client,
                task_config,
                portfolio_intent_service.build_draft_prompt(body),
                response_json_schema=portfolio_intent_service.response_schema(),
                schema_name="portfolio_intent_draft",
            ),
            timeout=_TASK_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        logger.warning("Portfolio Intent task timed out")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Portfolio Intent drafting timed out. Please try again.",
        ) from exc

    if not result.success:
        logger.warning("Portfolio Intent task failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Portfolio Intent drafting failed. Please try again.",
        )
    if await request.is_disconnected():
        raise HTTPException(status_code=499, detail="Portfolio Intent request was cancelled.")

    try:
        response = portfolio_intent_service.parse_draft_response(result.completion)
        return portfolio_intent_service.validate_response_for_request(response, body)
    except portfolio_intent_service.PortfolioIntentResponseError as exc:
        logger.warning("Portfolio Intent rejected the model response: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI returned an invalid portfolio intent. Please try again.",
        ) from exc
