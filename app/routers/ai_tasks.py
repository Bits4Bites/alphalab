from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app import config, dependencies, templating

router = APIRouter(tags=["ai_tasks"])


@router.get("/ai-tasks", response_class=HTMLResponse)
async def ai_tasks_page(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    return templating.templates.TemplateResponse(
        request, "ai_tasks.html", {"user": user, "tasks": config.ai_task_settings.tasks}
    )
