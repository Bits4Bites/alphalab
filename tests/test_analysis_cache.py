import datetime
import json
import types
from unittest import mock

import pytest

from app.services import analysis_cache
from app.utils import local_storage


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "123",
        "email": "test@example.com",
    }


def test_result_cache_key_extends_local_storage_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_cache.config.security_settings, "secret_key", "test-secret")
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "key_prefix", "al:")

    user_key = local_storage.derive_user_key(_user())

    assert analysis_cache.result_cache_key(_user(), "market-outlook") == f"al:{user_key}:market-outlook:result"


@pytest.mark.asyncio
async def test_set_cached_result_uses_seven_day_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = types.SimpleNamespace(set=mock.AsyncMock())
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_client", redis_client)

    persisted = await analysis_cache.set_cached_result(
        _user(),
        feature="market-outlook",
        inputs={"markets": "Australia"},
        content="# Outlook",
    )

    assert persisted is True
    call = redis_client.set.await_args
    assert call.args[0] == analysis_cache.result_cache_key(_user(), "market-outlook")
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
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_client", redis_client)

    result = await analysis_cache.get_cached_result(
        _user(),
        feature="market-outlook",
        input_fields=("markets",),
    )

    assert result == cached_result
    redis_client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_cached_result_removes_corrupted_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(return_value="{invalid"),
        delete=mock.AsyncMock(),
    )
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_client", redis_client)

    result = await analysis_cache.get_cached_result(
        _user(),
        feature="market-outlook",
        input_fields=("markets",),
    )

    assert result is None
    redis_client.delete.assert_awaited_once_with(analysis_cache.result_cache_key(_user(), "market-outlook"))


@pytest.mark.asyncio
async def test_set_cached_result_uses_memory_free_fallback_when_redis_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", False)

    persisted = await analysis_cache.set_cached_result(
        _user(),
        feature="market-outlook",
        inputs={"markets": "Global"},
        content="# Outlook",
    )

    assert persisted is False


@pytest.mark.asyncio
async def test_set_cached_payload_uses_seven_day_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = types.SimpleNamespace(set=mock.AsyncMock())
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_client", redis_client)

    persisted = await analysis_cache.set_cached_payload(
        _user(),
        feature="review-portfolio-rebalance",
        inputs={"holdings": "AAPL, 10"},
        payload={"content": "# Plan", "plan": {"market": "US"}},
    )

    assert persisted is True
    call = redis_client.set.await_args
    assert call.kwargs["ex"] == 7 * 24 * 60 * 60
    cached = json.loads(call.args[1])
    assert cached["payload"]["plan"] == {"market": "US"}
    assert datetime.datetime.fromisoformat(cached["generated_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_get_cached_payload_validates_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "holdings": "AAPL, 10",
        "payload": {"content": "# Plan", "plan": {"market": "US"}},
        "generated_at": "2026-07-22T09:00:00+10:00",
    }
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(return_value=json.dumps(cached_result)),
        delete=mock.AsyncMock(),
    )
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_client", redis_client)

    result = await analysis_cache.get_cached_payload(
        _user(),
        feature="review-portfolio-rebalance",
        input_fields=("holdings",),
        payload_validator=lambda value: value == cached_result["payload"],
    )

    assert result == cached_result
    redis_client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_cached_payload_removes_invalid_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "holdings": "AAPL, 10",
        "payload": {"content": "# Plan"},
        "generated_at": "2026-07-22T09:00:00+10:00",
    }
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(return_value=json.dumps(cached_result)),
        delete=mock.AsyncMock(),
    )
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_enabled", True)
    monkeypatch.setattr(analysis_cache.config.datastore_settings, "redis_client", redis_client)

    result = await analysis_cache.get_cached_payload(
        _user(),
        feature="review-portfolio-rebalance",
        input_fields=("holdings",),
        payload_validator=lambda _value: False,
    )

    assert result is None
    redis_client.delete.assert_awaited_once_with(analysis_cache.result_cache_key(_user(), "review-portfolio-rebalance"))
