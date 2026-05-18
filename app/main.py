from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routers import ai_tasks, ai_vendors, analyze_ticker, auth, build_portfolio, dashboard, health, review_portfolio

app = FastAPI(title="AlphaLab", description="AI-powered market research lab")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(analyze_ticker.router)
app.include_router(build_portfolio.router)
app.include_router(review_portfolio.router)
app.include_router(ai_vendors.router)
app.include_router(ai_tasks.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> RedirectResponse | HTMLResponse:
    if exc.status_code == 401:
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    from app.config import ai_vendor_settings, security_settings
    from app.services.oauth import get_enabled_providers

    enabled_providers = get_enabled_providers()
    configured_vendors = list(ai_vendor_settings.vendors.keys())
    allowed_emails = security_settings.allowed_emails

    return templates.TemplateResponse(request, "index.html", {
        "enabled_providers": enabled_providers,
        "configured_vendors": configured_vendors,
        "allowed_emails": allowed_emails,
    })
