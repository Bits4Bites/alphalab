from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user

router = APIRouter(tags=["analyze_ticker"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_page(request: Request, user: dict = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(request, "analyze_ticker.html", {"user": user})


@router.post("/analyze-ticker", response_class=HTMLResponse)
async def analyze_ticker_submit(
    request: Request, ticker: str = Form(...), user: dict = Depends(get_current_user)
) -> HTMLResponse:
    # TODO: Call AI model to analyze the ticker and return Markdown analysis
    analysis = "To be implemented"
    return templates.TemplateResponse(
        request, "analyze_ticker.html", {"user": user, "ticker": ticker, "analysis": analysis}
    )
