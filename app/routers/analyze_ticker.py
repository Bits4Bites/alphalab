import yfinance as yf
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user
from app.utils.ticker import to_yfinance_format

router = APIRouter(tags=["analyze_ticker"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, "analyze_ticker.html", {"user": user})


@router.post("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_submit(
    request: Request, ticker: str = Form(...), user: dict = Depends(get_current_user)
) -> HTMLResponse:
    context: dict = {"user": user, "ticker": ticker}

    yf_ticker = to_yfinance_format(ticker)
    if yf_ticker is None:
        context["error"] = f"Unsupported exchange in ticker '{ticker}'."
        return templates.TemplateResponse(request, "analyze_ticker.html", context)

    info = yf.Ticker(yf_ticker).info
    if not info or info.get("trailingPegRatio") is None and info.get("shortName") is None:
        context["error"] = f"Ticker '{ticker}' not found or invalid."
        return templates.TemplateResponse(request, "analyze_ticker.html", context)

    if info.get("quoteType") not in ("EQUITY", "ETF"):
        context["error"] = f"Ticker '{ticker}' is not tradeable."
        return templates.TemplateResponse(request, "analyze_ticker.html", context)

    # TODO: Call AI model to analyze the ticker and return Markdown analysis
    context["analysis"] = "To be implemented"
    context["ticker_info"] = info
    return templates.TemplateResponse(request, "analyze_ticker.html", context)
