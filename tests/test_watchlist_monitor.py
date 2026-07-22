import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import watchlist_monitor
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "543",
        "email": "watchlist@example.com",
        "name": "Watchlist User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "WATCHLIST_MONITOR_BUILD_PROMPT": task,
            "WATCHLIST_MONITOR_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated watchlist prompt"),
            ai.AIResponse(success=True, completion="# Watchlist analysis"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(watchlist_monitor.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(watchlist_monitor.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(watchlist_monitor.analysis_cache, "set_cached_result", set_cached_result)

    response = await watchlist_monitor.watchlist_monitor_stream(
        Request({"type": "http", "method": "GET", "path": "/watchlist-monitor/stream", "headers": []}),
        tickers=" AAPL, MSFT, NVDA ",
        target_market=" US ",
        focus=" Earnings momentum ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="watchlist-monitor",
        inputs={
            "tickers": "AAPL, MSFT, NVDA",
            "target_market": "US",
            "focus": "Earnings momentum",
        },
        content="# Watchlist analysis",
    )
    assert events[-1] == {"type": "result", "content": "# Watchlist analysis"}


def test_page_embeds_cached_result_and_browser_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "tickers": "AAPL, MSFT, NVDA",
        "target_market": "US",
        "focus": "Earnings momentum",
        "content": "# Cached watchlist analysis",
        "generated_at": "2026-07-22T11:55:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(watchlist_monitor.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/watchlist-monitor")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "543"
    assert cache_call.kwargs == {
        "feature": "watchlist-monitor",
        "input_fields": ("tickers", "target_market", "focus"),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached watchlist analysis" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    save_call = response.text.split("window.AlphaLabStorage.save(", maxsplit=1)[1].split(");", maxsplit=1)[0]
    for field in ("tickers", "target_market", "focus"):
        assert f"{field}:" in save_call
