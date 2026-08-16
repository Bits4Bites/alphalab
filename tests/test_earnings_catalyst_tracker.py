import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import earnings_catalyst_tracker
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "765",
        "email": "earnings@example.com",
        "name": "Earnings User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "EARNINGS_CATALYST_TRACKER_BUILD_PROMPT": task,
            "EARNINGS_CATALYST_TRACKER_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated earnings prompt"),
            ai.AIResponse(success=True, completion="# Earnings catalyst analysis"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(earnings_catalyst_tracker.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(earnings_catalyst_tracker.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(earnings_catalyst_tracker.analysis_cache, "set_cached_result", set_cached_result)

    response = await earnings_catalyst_tracker.earnings_catalyst_tracker_stream(
        Request({"type": "http", "method": "GET", "path": "/earnings-catalyst-tracker/stream", "headers": []}),
        tickers=" AAPL, MSFT, NVDA ",
        target_market=" US ",
        event_focus=" Guidance risk ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="earnings-catalyst-tracker",
        inputs={
            "tickers": "AAPL, MSFT, NVDA",
            "target_market": "US",
            "event_focus": "Guidance risk",
        },
        content="# Earnings catalyst analysis",
    )
    assert events[-1] == {"type": "result", "content": "# Earnings catalyst analysis"}


def test_page_embeds_cached_result_and_browser_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "tickers": "AAPL, MSFT, NVDA",
        "target_market": "US",
        "event_focus": "Guidance risk",
        "content": "# Cached earnings catalyst analysis",
        "generated_at": "2026-07-22T12:05:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(earnings_catalyst_tracker.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/earnings-catalyst-tracker")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "765"
    assert cache_call.kwargs == {
        "feature": "earnings-catalyst-tracker",
        "input_fields": ("tickers", "target_market", "event_focus"),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached earnings catalyst analysis" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    save_call = response.text.split("window.AlphaLabStorage.save(", maxsplit=1)[1].split(");", maxsplit=1)[0]
    for field in ("tickers", "target_market", "event_focus"):
        assert f"{field}:" in save_call
