import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import analyze_ticker
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "789",
        "email": "ticker@example.com",
        "name": "Ticker User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "ANALYZE_TICKER_BUILD_PROMPT": task,
            "ANALYZE_TICKER_ANALYZE_QUICK": task,
            "ANALYZE_TICKER_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated analysis prompt"),
            ai.AIResponse(success=True, completion="# Ticker analysis"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    ticker_info = {
        "quoteType": "EQUITY",
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "fullExchangeName": "NASDAQ",
        "marketCap": 2_500_000_000_000,
        "country": "United States",
    }
    monkeypatch.setattr(analyze_ticker.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(analyze_ticker.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(analyze_ticker.analysis_cache, "set_cached_result", set_cached_result)
    monkeypatch.setattr(analyze_ticker.ticker_utils, "to_yfinance_format", lambda ticker: "AAPL")
    monkeypatch.setattr(analyze_ticker.yf, "Ticker", lambda ticker: types.SimpleNamespace(info=ticker_info))

    response = await analyze_ticker.analyze_ticker_stream(
        Request({"type": "http", "method": "GET", "path": "/analyze-ticker/stream", "headers": []}),
        ticker=" NASDAQ:AAPL ",
        quick_mode=True,
        intent=" Long-term growth ",
        scenario=" Rate shock ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="analyze-ticker",
        inputs={
            "ticker": "NASDAQ:AAPL",
            "quick_mode": "true",
            "intent": "Long-term growth",
            "scenario": "Rate shock",
        },
        content="# Ticker analysis",
    )
    assert events[-1] == {"type": "result", "content": "# Ticker analysis"}


def test_page_embeds_cached_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "ticker": "NASDAQ:AAPL",
        "quick_mode": "true",
        "intent": "Long-term growth",
        "scenario": "",
        "content": "# Cached ticker analysis",
        "generated_at": "2026-07-22T10:30:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(analyze_ticker.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/analyze-ticker")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "789"
    assert cache_call.kwargs == {
        "feature": "analyze-ticker",
        "input_fields": ("ticker", "quick_mode", "intent", "scenario"),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached ticker analysis" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
