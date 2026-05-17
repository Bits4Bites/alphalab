from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import ai_task_settings
from app.dependencies import get_current_user

router = APIRouter(tags=["ai_tasks"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/ai-tasks", response_class=HTMLResponse)
async def ai_tasks_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, "ai_tasks.html", {"user": user, "tasks": ai_task_settings.tasks})
