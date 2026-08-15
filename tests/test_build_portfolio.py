import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import build_portfolio
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "321",
        "email": "builder@example.com",
        "name": "Portfolio Builder",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1, web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "BUILD_PORTFOLIO_BUILD_PROMPT": task,
            "BUILD_PORTFOLIO_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated portfolio prompt"),
            ai.AIResponse(success=True, completion="# Portfolio recommendation"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(build_portfolio.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(build_portfolio.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(build_portfolio.analysis_cache, "set_cached_result", set_cached_result)

    response = await build_portfolio.build_portfolio_stream(
        Request({"type": "http", "method": "GET", "path": "/build-portfolio/stream", "headers": []}),
        risk_tolerance=" Moderate ",
        investment_theme=" Global quality ",
        target_market=" Global ",
        investment_horizon=" Long-term (3-5 years) ",
        budget=" $50,000 ",
        existing_holdings=" AAPL 20 shares ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="build-portfolio",
        inputs={
            "risk_tolerance": "Moderate",
            "investment_theme": "Global quality",
            "target_market": "Global",
            "investment_horizon": "Long-term (3-5 years)",
            "budget": "$50,000",
            "existing_holdings": "AAPL 20 shares",
        },
        content="# Portfolio recommendation",
    )
    assert events[-1] == {"type": "result", "content": "# Portfolio recommendation"}


def test_page_embeds_cached_result_and_browser_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "risk_tolerance": "Moderate",
        "investment_theme": "Global quality",
        "target_market": "Global",
        "investment_horizon": "Long-term (3-5 years)",
        "budget": "$50,000",
        "existing_holdings": "AAPL 20 shares",
        "content": "# Cached portfolio recommendation",
        "generated_at": "2026-07-22T11:30:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(build_portfolio.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/build-portfolio")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "321"
    assert cache_call.kwargs == {
        "feature": "build-portfolio",
        "input_fields": (
            "risk_tolerance",
            "investment_theme",
            "target_market",
            "investment_horizon",
            "budget",
            "existing_holdings",
        ),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached portfolio recommendation" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    save_call = response.text.split("window.AlphaLabStorage.save(", maxsplit=1)[1].split(");", maxsplit=1)[0]
    for field in (
        "risk_tolerance",
        "investment_theme",
        "target_market",
        "investment_horizon",
        "budget",
        "existing_holdings",
    ):
        assert f"{field}:" in save_call
