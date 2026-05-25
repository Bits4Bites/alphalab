import json

import country_converter as coco
import yfinance as yf
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app.config import ai_task_settings
from app.dependencies import get_current_user
from app.templating import templates
from app.utils.ai import execute_prompt
from app.utils.ticker import to_yfinance_format

router = APIRouter(tags=["analyze_ticker"])
TEMPLATE = "analyze_ticker.html"

_BASE_PROMPT_TEMPLATE = (
    "You are an expert financial analyst and prompt engineer.\n"
    "\n"
    "Your task is to write a {depth_word}, ready-to-execute prompt that instructs a premium AI model "
    "to perform a {depth_phrase} stock analysis.\n"
    "\n"
    "## Asset to analyze\n"
    "- Ticker:     {ticker}\n"
    "- Name:       {name}\n"
    "- Asset type: {asset_type}\n"
    "{sector_industry}"
    "- Exchange:   {exchange}\n"
    "- Market cap: {market_cap_tier}\n"
    "\n"
    "## Your instructions\n"
    "Adapt the analysis prompt to the nature of this specific asset:\n"
    "- For ETFs/funds: focus on holdings, expense ratio, tracking error, liquidity\n"
    "- For REITs: focus on FFO, AFFO, occupancy, dividend sustainability, debt structure\n"
    "- For foreign equities: include currency risk, local regulations, geopolitical factors\n"
    "- For small/micro-cap: flag liquidity risk, limited analyst coverage, higher volatility\n"
    "- For sector-specific equities: include the key metrics that matter most for that sector\n"
    "\n"
    "Write a {depth_word} prompt that tells the premium model to:\n"
    "1. Use its web search capability to gather current, real data on the stock\n"
    "2. Structure the analysis clearly with defined sections\n"
    "3. Support every claim with data (numbers, dates, sources)\n"
    "4. Conclude with a balanced, evidence-based investment view\n"
    "\n"
    "## The prompt must instruct the premium model to cover (adapted to asset type):\n"
    "{cover_items}\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-use prompt. No preamble, no explanation, no commentary.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)

_QUICK_COVER_ITEMS = (
    "- Brief asset overview (business model or fund objective, sector, market cap tier)\n"
    "- Current stock quotation summary (price, recent change, key multiples)\n"
    "- Stock outlook with trend prediction for the next 2 weeks, 1 month, and 3 months\n"
    "- Confidence level (low/medium/high) for each outlook period\n"
    "- A brief final investment summary (bullish / neutral / bearish case)"
)

_FULL_COVER_ITEMS = (
    "- Asset overview (business model or fund objective, sector, market cap tier)\n"
    "- Recent financial performance (metrics relevant to this asset type)\n"
    "- Valuation (multiples relevant to this asset type vs. appropriate peers)\n"
    "- Growth catalysts and risks (including country/currency risk if applicable)\n"
    "- Recent news and sentiment (last 30 days)\n"
    "- Analyst or fund consensus and price/NAV targets where available\n"
    "- Stock outlook with trend prediction for the next 2 weeks, 1 month, and 3 months\n"
    "- Confidence level (low/medium/high) for each outlook period\n"
    "- A final investment summary (bullish / neutral / bearish case)"
)


# Market-cap tier thresholds per country/market (in local currency).
# Each tuple: (mega, large, mid, small, micro) — values are lower bounds for each tier.
# Sources: index methodology from S&P, ASX, TSE, etc.
# Precision is not critical — these are only used to bucket market caps into tiers.
_MARKET_CAP_TIERS: dict[str, tuple[float, float, float, float, float]] = {
    "US": (200e9, 10e9, 2e9, 300e6, 50e6),
    "CA": (30e9, 5e9, 1e9, 250e6, 50e6),
    "AU": (50e9, 10e9, 2e9, 300e6, 50e6),
    "NZ": (10e9, 2e9, 500e6, 100e6, 20e6),
    "GB": (20e9, 4e9, 1e9, 150e6, 30e6),
    "EU": (50e9, 10e9, 2e9, 300e6, 50e6),  # Eurozone catch-all
    "DE": (50e9, 10e9, 2e9, 300e6, 50e6),
    "FR": (50e9, 10e9, 2e9, 300e6, 50e6),
    "CH": (100e9, 20e9, 5e9, 1e9, 200e6),
    "JP": (10e12, 1e12, 300e9, 50e9, 10e9),
    "CN": (500e9, 100e9, 20e9, 5e9, 1e9),
    "HK": (500e9, 50e9, 10e9, 2e9, 500e6),
    "TW": (1e12, 100e9, 30e9, 5e9, 1e9),
    "KR": (100e12, 10e12, 2e12, 500e9, 100e9),
    "SG": (20e9, 5e9, 1e9, 200e6, 50e6),
    "IN": (2e12, 500e9, 100e9, 20e9, 5e9),
    "VN": (100e12, 30e12, 5e12, 1e12, 200e9),
    "TH": (500e9, 100e9, 20e9, 5e9, 1e9),
    "ID": (200e12, 50e12, 10e12, 2e12, 500e9),
    "MY": (50e9, 10e9, 2e9, 500e6, 100e6),
    "PH": (500e9, 100e9, 20e9, 5e9, 1e9),
    "BR": (100e9, 20e9, 5e9, 1e9, 200e6),
    "MX": (500e9, 100e9, 20e9, 5e9, 1e9),
    "ZA": (500e9, 100e9, 20e9, 5e9, 1e9),
}

_TIER_LABELS = ("Mega-cap", "Large-cap", "Mid-cap", "Small-cap", "Micro-cap", "Nano-cap")

# Singleton converter instance (avoids repeated init overhead)
_COUNTRY_CONVERTER = coco.CountryConverter()

# Keywords used for asset type heuristics
_ETF_KEYWORDS = {"etf", "ishares", "vanguard", "spdr", "proshares", "invesco", "wisdomtree", "vaneck", "direxion"}
_FUND_KEYWORDS = {"fund", "mutual", "trust", "income fund", "growth fund", "balanced fund", "bond fund"}
_REIT_KEYWORDS = {"reit", "real estate investment trust", "property trust", "realty"}


def _detect_asset_type(info: dict) -> str:
    """Detect asset type using multiple yfinance fields.

    Returns one of: 'ETF', 'MUTUALFUND', 'REIT', 'EQUITY'.
    """
    quote_type = (info.get("quoteType") or "").upper()

    # quoteType already distinguishes ETF and MUTUALFUND
    if quote_type == "ETF":
        return "ETF"
    if quote_type == "MUTUALFUND":
        return "MUTUALFUND"

    # For EQUITY, further classify as REIT or plain EQUITY using heuristics
    name_lower = ((info.get("longName") or "") + " " + (info.get("shortName") or "")).lower()
    summary_lower = (info.get("longBusinessSummary") or "").lower()
    sector = (info.get("sector") or "").lower()
    industry = (info.get("industry") or "").lower()

    # REIT detection
    if "real estate" in sector or "reit" in industry:
        return "REIT"
    if any(kw in name_lower for kw in _REIT_KEYWORDS):
        return "REIT"
    if any(kw in summary_lower for kw in _REIT_KEYWORDS):
        return "REIT"

    # ETF detection fallback (some ETFs report quoteType as EQUITY)
    if any(kw in name_lower for kw in _ETF_KEYWORDS):
        return "ETF"

    # Mutual fund detection fallback
    if any(kw in name_lower for kw in _FUND_KEYWORDS):
        return "MUTUALFUND"

    return "EQUITY"


def _country_to_iso2(country_name: str | None) -> str | None:
    """Convert a country name (e.g. 'United States') to ISO2 code (e.g. 'US')."""
    if not country_name:
        return None
    code = _COUNTRY_CONVERTER.convert(country_name, to="ISO2")
    return code if code != "not found" else None


def _market_cap_tier(market_cap: int | float | None, country: str | None = None) -> str:
    """Map raw market cap to a tier label using country-specific thresholds.

    Each market has its own index-based definitions of large/mid/small cap,
    so thresholds are in local currency — no FX conversion needed.
    Falls back to US thresholds when country is unknown.
    """
    if market_cap is None or market_cap < 0:
        return "(unknown)"
    thresholds = _MARKET_CAP_TIERS.get(country or "US", _MARKET_CAP_TIERS["US"])
    for i, threshold in enumerate(thresholds):
        if market_cap > threshold:
            return _TIER_LABELS[i]
    return _TIER_LABELS[-1]


def _build_analysis_prompt(*, ticker: str, info: dict, quick_mode: bool) -> str:
    """Build the meta-prompt that instructs the AI to produce a stock analysis prompt."""
    name = info.get("longName") or info.get("shortName") or ""
    country = _country_to_iso2(info.get("country"))
    asset_type = _detect_asset_type(info)

    # Sector/Industry only relevant for EQUITY and REIT
    if asset_type in ("EQUITY", "REIT"):
        sector_industry = (
            f"- Sector:     {info.get('sector') or '(n/a)'}\n- Industry:   {info.get('industry') or '(n/a)'}\n"
        )
    else:
        sector_industry = ""

    return _BASE_PROMPT_TEMPLATE.format(
        depth_word="concise" if quick_mode else "detailed",
        depth_phrase="one-page, focused" if quick_mode else "thorough",
        ticker=ticker,
        name=name,
        asset_type=asset_type,
        sector_industry=sector_industry,
        exchange=info.get("fullExchangeName") or info.get("exchange") or "(n/a)",
        market_cap_tier=_market_cap_tier(info.get("marketCap"), country),
        cover_items=_QUICK_COVER_ITEMS if quick_mode else _FULL_COVER_ITEMS,
    )


@router.get("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, TEMPLATE, {"user": user})


@router.get("/analyze-ticker/stream")
async def analyze_ticker_stream(
    request: Request,
    ticker: str = Query(...),
    quick_mode: bool = Query(default=False),
    user: dict = Depends(get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def progress(step: int, total: int, message: str):
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str):
            return json.dumps({"type": "error", "message": message})

        def result(content: str):
            return json.dumps({"type": "result", "content": content})

        total_steps = 5

        # Step 1: Validate ticker format
        yield {"data": progress(1, total_steps, "Validating ticker format...")}
        yf_ticker = to_yfinance_format(ticker)
        if yf_ticker is None:
            yield {"data": error(f"Unsupported exchange in ticker '{ticker}'.")}
            return

        # Step 2: Fetch ticker info
        yield {"data": progress(2, total_steps, "Fetching ticker information...")}
        info = yf.Ticker(yf_ticker).info
        if not info or info.get("quoteType") not in ("EQUITY", "ETF"):
            yield {"data": error(f"Ticker '{ticker}' not found or invalid or not tradeable.")}
            return

        # Step 3: Generate analysis prompt
        yield {"data": progress(3, total_steps, "Generating analysis prompt...")}
        build_prompt_client = ai_task_settings.get_ai_client("ANALYZE_TICKER_BUILD_PROMPT")
        if not build_prompt_client:
            yield {"data": error("AI task 'ANALYZE_TICKER_BUILD_PROMPT' is not configured.")}
            return

        build_prompt_task = ai_task_settings.tasks.get("ANALYZE_TICKER_BUILD_PROMPT")
        prompt_request = _build_analysis_prompt(ticker=ticker, info=info, quick_mode=quick_mode)
        prompt_result = await execute_prompt(build_prompt_client, build_prompt_task.model, prompt_request)

        if not prompt_result.success:
            yield {"data": error(f"Failed to generate analysis prompt: {prompt_result.error}")}
            return

        # ### DEBUG: START
        # yield {"data": progress(total_steps, total_steps, "Analysis complete!")}
        # yield {"data": result(prompt_result.completion)}
        # return
        # ### DEBUG: END

        # Step 4: Analyze ticker with generated prompt
        analyze_task_id = "ANALYZE_TICKER_ANALYZE_QUICK" if quick_mode else "ANALYZE_TICKER_ANALYZE"
        yield {"data": progress(4, total_steps, "Analyzing ticker with AI...")}
        analyze_client = ai_task_settings.get_ai_client(analyze_task_id)
        if not analyze_client:
            yield {"data": error(f"AI task '{analyze_task_id}' is not configured.")}
            return

        analyze_task = ai_task_settings.tasks.get(analyze_task_id)
        analysis_result = await execute_prompt(analyze_client, analyze_task.model, prompt_result.completion)

        if not analysis_result.success:
            yield {"data": error(f"Failed to analyze ticker: {analysis_result.error}")}
            return

        # Step 5: Done
        yield {"data": progress(5, total_steps, "Analysis complete!")}
        yield {"data": result(analysis_result.completion)}

    return EventSourceResponse(event_generator())
