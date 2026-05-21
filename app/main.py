import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routers import ai_tasks, ai_vendors, analyze_ticker, auth, build_portfolio, dashboard, health, review_portfolio
from app.templating import templates

logger = logging.getLogger(__name__)


async def _check_redis() -> None:
    """Check Redis connectivity and set datastore_redis_enabled flag."""
    from redis.asyncio import from_url

    from app.config import datastore_settings

    try:
        client = from_url(datastore_settings.redis_url, decode_responses=True)
        await client.ping()
        datastore_settings.redis_enabled = True
        datastore_settings.redis_client = client
        logger.info("Redis connection OK")
    except Exception as exc:
        datastore_settings.redis_enabled = False
        datastore_settings.redis_client = None
        logger.warning("Redis unavailable, data store disabled: %s", exc)


async def _startup_background_tasks() -> None:
    """Run startup background tasks: Redis check, then sample prompt generation."""
    await _check_redis()

    from app.config import datastore_settings

    if datastore_settings.redis_enabled:
        try:
            from app.services.sample_prompts import generate_sample_prompts

            await generate_sample_prompts()
        except Exception as exc:
            logger.error("Sample prompt generation failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_startup_background_tasks())
    yield


app = FastAPI(title="AlphaLab", description="AI-powered market research lab", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

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
    from app.config import ai_vendor_settings, app_settings, security_settings
    from app.services.oauth import get_enabled_providers

    enabled_providers = get_enabled_providers()
    configured_vendors = list(ai_vendor_settings.vendors.keys())
    allowed_emails = security_settings.allowed_emails
    primary_markets = sorted(app_settings.primary_markets)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "enabled_providers": enabled_providers,
            "configured_vendors": configured_vendors,
            "allowed_emails": allowed_emails,
            "primary_markets": primary_markets,
        },
    )


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "terms.html")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "privacy.html")
