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
    compare_investments,
    dashboard,
    dividend_event,
    earnings_catalyst_tracker,
    health,
    ipo_analyzer,
    ipo_scanner,
    market_outlook,
    portfolio_action_briefing,
    review_portfolio,
    sector_rotation_radar,
    watchlist_monitor,
)
from app.utils import scheduler as scheduler_mod

logger = logging.getLogger(__name__)
scheduler = scheduler_mod.BackgroundScheduler()

# Tracks the last known Redis availability so the periodic check logs only on
# state transitions (None = unknown / not yet checked).
_redis_was_enabled: bool | None = None


async def _check_redis() -> None:
    """Check Redis connectivity and toggle the data store enabled flag.

    Safe to call repeatedly from a periodic task: it reuses the existing client
    when possible, only creating a new connection when one is not established, and
    logs only on state transitions to avoid noisy repeated messages.
    """
    global _redis_was_enabled

    from redis import asyncio as redis_asyncio

    from app import config

    settings = config.datastore_settings
    try:
        client = settings.redis_client
        if client is None:
            client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        settings.redis_enabled = True
        settings.redis_client = client
        if _redis_was_enabled is not True:
            logger.info("Redis connection OK, data store enabled")
        _redis_was_enabled = True
    except Exception as exc:
        settings.redis_enabled = False
        settings.redis_client = None
        if _redis_was_enabled is not False:
            logger.warning("Redis unavailable, data store disabled: %s", exc)
        _redis_was_enabled = False


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
        name="check_redis",
        func=_check_redis,
        interval_seconds=60,
        run_on_start=False,
    )
)
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
app.include_router(compare_investments.router)
app.include_router(build_portfolio.router)
app.include_router(portfolio_action_briefing.router)
app.include_router(review_portfolio.router)
app.include_router(dividend_event.router)
app.include_router(market_outlook.router)
app.include_router(sector_rotation_radar.router)
app.include_router(ipo_scanner.router)
app.include_router(watchlist_monitor.router)
app.include_router(earnings_catalyst_tracker.router)
app.include_router(ipo_analyzer.router)
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
