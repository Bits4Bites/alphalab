import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import templating
from app.routers import (
    ai_tasks,
    ai_vendors,
    analyze_ticker,
    auth,
    build_portfolio,
    dashboard,
    dividend_event,
    health,
    market_outlook,
    review_portfolio,
)
from app.utils import scheduler as scheduler_mod

logger = logging.getLogger(__name__)
scheduler = scheduler_mod.BackgroundScheduler()


async def _check_redis() -> None:
    """Check Redis connectivity and set datastore_redis_enabled flag."""
    from redis import asyncio as redis_asyncio

    from app import config

    try:
        client = redis_asyncio.from_url(config.datastore_settings.redis_url, decode_responses=True)
        await client.ping()
        config.datastore_settings.redis_enabled = True
        config.datastore_settings.redis_client = client
        logger.info("Redis connection OK")
    except Exception as exc:
        config.datastore_settings.redis_enabled = False
        config.datastore_settings.redis_client = None
        logger.warning("Redis unavailable, data store disabled: %s", exc)


async def _generate_sample_prompts_task() -> None:
    """Wrapper for sample prompt generation that checks Redis availability."""
    from app import config

    if not config.datastore_settings.redis_enabled:
        return

    from app.services import sample_prompts

    await sample_prompts.generate_sample_prompts()


async def _fetch_market_news_task() -> None:
    """Wrapper for market news fetch that checks Redis availability."""
    from app import config

    if not config.datastore_settings.redis_enabled:
        return

    from app.services import market_news

    await market_news.fetch_market_news()


# Register periodic tasks
scheduler.register(
    scheduler_mod.PeriodicTask(
        name="generate_sample_prompts",
        func=_generate_sample_prompts_task,
        interval_seconds=3600,
        run_on_start=True,
    )
)
scheduler.register(
    scheduler_mod.PeriodicTask(
        name="fetch_market_news",
        func=_fetch_market_news_task,
        interval_seconds=3600,
        run_on_start=True,
    )
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _check_redis()
    await scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(
    title="AlphaLab",
    description="AI-powered market research lab",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(analyze_ticker.router)
app.include_router(build_portfolio.router)
app.include_router(review_portfolio.router)
app.include_router(dividend_event.router)
app.include_router(market_outlook.router)
app.include_router(ai_vendors.router)
app.include_router(ai_tasks.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> RedirectResponse | HTMLResponse:
    if exc.status_code == 401:
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    from app import config
    from app.services import oauth

    enabled_providers = oauth.get_enabled_providers()
    configured_vendors = list(config.ai_vendor_settings.vendors.keys())
    allowed_emails = config.security_settings.allowed_emails
    primary_markets = sorted(config.app_settings.primary_markets)

    return templating.templates.TemplateResponse(
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
    return templating.templates.TemplateResponse(request, "terms.html")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templating.templates.TemplateResponse(request, "privacy.html")
