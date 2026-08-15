import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import market_outlook
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "123",
        "email": "test@example.com",
        "name": "Test User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1, web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "MARKET_OUTLOOK_BUILD_PROMPT": task,
            "MARKET_OUTLOOK_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated analysis prompt"),
            ai.AIResponse(success=True, completion="# Fresh outlook"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(market_outlook.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(market_outlook.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(market_outlook.analysis_cache, "set_cached_result", set_cached_result)

    response = await market_outlook.market_outlook_stream(
        Request({"type": "http", "method": "GET", "path": "/market-outlook/stream", "headers": []}),
        markets=" Australia ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="market-outlook",
        inputs={"markets": "Australia"},
        content="# Fresh outlook",
    )
    assert events[-1] == {"type": "result", "content": "# Fresh outlook"}


def test_page_embeds_cached_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "markets": "Australia",
        "content": "# Cached outlook",
        "generated_at": "2026-07-22T09:00:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(market_outlook.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/market-outlook")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "123"
    assert cache_call.kwargs == {
        "feature": "market-outlook",
        "input_fields": ("markets",),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached outlook" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
