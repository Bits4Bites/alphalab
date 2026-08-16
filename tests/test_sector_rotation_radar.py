import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import sector_rotation_radar
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "456",
        "email": "sector@example.com",
        "name": "Sector User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "SECTOR_ROTATION_RADAR_BUILD_PROMPT": task,
            "SECTOR_ROTATION_RADAR_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated analysis prompt"),
            ai.AIResponse(success=True, completion="# Sector outlook"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(sector_rotation_radar.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(sector_rotation_radar.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(sector_rotation_radar.analysis_cache, "set_cached_result", set_cached_result)

    response = await sector_rotation_radar.sector_rotation_radar_stream(
        Request({"type": "http", "method": "GET", "path": "/sector-rotation-radar/stream", "headers": []}),
        target_market=" US ",
        sectors=" Technology, Energy ",
        timeframe=" 1 month ",
        bias=" growth ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="sector-rotation-radar",
        inputs={
            "target_market": "US",
            "sectors": "Technology, Energy",
            "timeframe": "1 month",
            "bias": "growth",
        },
        content="# Sector outlook",
    )
    assert events[-1] == {"type": "result", "content": "# Sector outlook"}


def test_page_embeds_cached_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "target_market": "US",
        "sectors": "Technology",
        "timeframe": "1 month",
        "bias": "growth",
        "content": "# Cached sector outlook",
        "generated_at": "2026-07-22T10:00:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(sector_rotation_radar.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/sector-rotation-radar")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "456"
    assert cache_call.kwargs == {
        "feature": "sector-rotation-radar",
        "input_fields": ("target_market", "sectors", "timeframe", "bias"),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached sector outlook" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
