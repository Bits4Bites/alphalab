from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app import config, dependencies, templating

router = APIRouter(tags=["ai_vendors"])


@router.get("/ai-vendors", response_class=HTMLResponse)
async def ai_vendors_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    return templating.templates.TemplateResponse(
        request, "ai_vendors.html", {"user": user, "vendors": config.ai_vendor_settings.vendors}
    )
