import datetime
import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import market_outlook
from app.services import auth
from app.utils import ai, local_storage


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "123",
        "email": "test@example.com",
        "name": "Test User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "MARKET_OUTLOOK_BUILD_PROMPT": task,
            "MARKET_OUTLOOK_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


def test_cache_key_extends_local_storage_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_outlook.config.security_settings, "secret_key", "test-secret")
    monkeypatch.setattr(market_outlook.config.datastore_settings, "key_prefix", "al:")

    user_key = local_storage.derive_user_key(_user())

    assert market_outlook._cache_key(_user()) == f"al:{user_key}:market-outlook:result"


@pytest.mark.asyncio
async def test_set_cached_result_uses_seven_day_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = types.SimpleNamespace(set=mock.AsyncMock())
    monkeypatch.setattr(market_outlook.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(market_outlook.config.datastore_settings, "redis_client", redis_client)

    await market_outlook._set_cached_result(
        _user(),
        markets="Australia",
        content="# Outlook",
    )

    call = redis_client.set.await_args
    assert call.args[0] == market_outlook._cache_key(_user())
    assert call.kwargs["ex"] == 7 * 24 * 60 * 60
    payload = json.loads(call.args[1])
    assert payload["markets"] == "Australia"
    assert payload["content"] == "# Outlook"
    assert datetime.datetime.fromisoformat(payload["generated_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_get_cached_result_returns_valid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "markets": "Global",
        "content": "# Cached outlook",
        "generated_at": "2026-07-22T09:00:00+10:00",
    }
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(return_value=json.dumps(cached_result)),
        delete=mock.AsyncMock(),
    )
    monkeypatch.setattr(market_outlook.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(market_outlook.config.datastore_settings, "redis_client", redis_client)

    result = await market_outlook._get_cached_result(_user())

    assert result == cached_result
    redis_client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_cached_result_removes_corrupted_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(return_value="{invalid"),
        delete=mock.AsyncMock(),
    )
    monkeypatch.setattr(market_outlook.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(market_outlook.config.datastore_settings, "redis_client", redis_client)

    result = await market_outlook._get_cached_result(_user())

    assert result is None
    redis_client.delete.assert_awaited_once_with(market_outlook._cache_key(_user()))


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated analysis prompt"),
            ai.AIResponse(success=True, completion="# Fresh outlook"),
        ]
    )
    set_cached_result = mock.AsyncMock()
    monkeypatch.setattr(market_outlook.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(market_outlook.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(market_outlook, "_set_cached_result", set_cached_result)

    response = await market_outlook.market_outlook_stream(
        Request({"type": "http", "method": "GET", "path": "/market-outlook/stream", "headers": []}),
        markets=" Australia ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        markets="Australia",
        content="# Fresh outlook",
    )
    assert events[-1] == {"type": "result", "content": "# Fresh outlook"}


def test_page_embeds_cached_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "markets": "Australia",
        "content": "# Cached outlook",
        "generated_at": "2026-07-22T09:00:00+10:00",
    }
    monkeypatch.setattr(
        market_outlook,
        "_get_cached_result",
        mock.AsyncMock(return_value=cached_result),
    )
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/market-outlook")

    assert response.status_code == 200
    assert 'id="cached-result-data"' in response.text
    assert "# Cached outlook" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
