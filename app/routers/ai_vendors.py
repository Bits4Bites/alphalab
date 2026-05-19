from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.config import ai_vendor_settings
from app.dependencies import get_current_user
from app.templating import templates

router = APIRouter(tags=["ai_vendors"])


@router.get("/ai-vendors", response_class=HTMLResponse)
async def ai_vendors_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, "ai_vendors.html", {"user": user, "vendors": ai_vendor_settings.vendors})
