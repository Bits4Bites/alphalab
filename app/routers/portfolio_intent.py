from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app import config, dependencies, templating
from app.schemas import portfolio_intent as portfolio_intent_schemas
from app.services import portfolio_intent as portfolio_intent_service
from app.utils import ai

router = APIRouter(tags=["portfolio_intent"])
_TASK_ID = "DRAFT_PORTFOLIO_INTENT"
_TEMPLATE = "draft_portfolio_intent.html"


@router.get("/draft-portfolio-intent", response_class=HTMLResponse)
async def draft_portfolio_intent_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    return templating.templates.TemplateResponse(request, _TEMPLATE, {"user": user})


@router.post("/portfolio-intent/draft", response_model=portfolio_intent_schemas.DraftIntentResponse)
async def draft_portfolio_intent(
    body: portfolio_intent_schemas.DraftIntentRequest,
    _user: dict = Depends(dependencies.get_current_user),
) -> portfolio_intent_schemas.DraftIntentResponse:
    task_config = config.ai_task_settings.tasks.get(_TASK_ID)
    client = config.ai_task_settings.get_ai_client(_TASK_ID)
    if task_config is None or client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI task '{_TASK_ID}' is not configured.",
        )

    result = await ai.execute_task_prompt(
        client,
        task_config,
        portfolio_intent_service.build_draft_prompt(body),
        response_json_schema=portfolio_intent_schemas.DraftIntentResponse.model_json_schema(),
        schema_name="portfolio_intent_draft",
    )
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to draft portfolio intent: {result.error}",
        )

    try:
        return portfolio_intent_service.parse_draft_response(result.completion)
    except portfolio_intent_service.PortfolioIntentResponseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
