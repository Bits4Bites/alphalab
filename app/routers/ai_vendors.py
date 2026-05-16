from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import ai_vendor_settings
from app.dependencies import get_current_user

router = APIRouter(tags=["ai_vendors"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/ai-vendors", response_class=HTMLResponse)
async def ai_vendors_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, "ai_vendors.html", {"user": user, "vendors": ai_vendor_settings.vendors})
