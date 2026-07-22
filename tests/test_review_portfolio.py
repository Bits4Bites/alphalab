import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import review_portfolio
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "432",
        "email": "reviewer@example.com",
        "name": "Portfolio Reviewer",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "REVIEW_PORTFOLIO_BUILD_PROMPT": task,
            "REVIEW_PORTFOLIO_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated review prompt"),
            ai.AIResponse(success=True, completion="# Portfolio review"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_portfolio.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_portfolio.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(review_portfolio.analysis_cache, "set_cached_result", set_cached_result)

    response = await review_portfolio.review_portfolio_stream(
        Request({"type": "http", "method": "GET", "path": "/review-portfolio/stream", "headers": []}),
        holdings=" AAPL 20 shares, BND 10 shares ",
        risk_tolerance=" Conservative ",
        investment_goals=" Retirement income ",
        target_market=" US ",
        investment_horizon=" Very long-term (5+ years) ",
        scenario=" Rate shock ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="review-portfolio",
        inputs={
            "holdings": "AAPL 20 shares, BND 10 shares",
            "risk_tolerance": "Conservative",
            "investment_goals": "Retirement income",
            "target_market": "US",
            "investment_horizon": "Very long-term (5+ years)",
            "scenario": "Rate shock",
        },
        content="# Portfolio review",
    )
    assert events[-1] == {"type": "result", "content": "# Portfolio review"}


def test_page_embeds_cached_result_and_browser_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "holdings": "AAPL 20 shares, BND 10 shares",
        "risk_tolerance": "Conservative",
        "investment_goals": "Retirement income",
        "target_market": "US",
        "investment_horizon": "Very long-term (5+ years)",
        "scenario": "Rate shock",
        "content": "# Cached portfolio review",
        "generated_at": "2026-07-22T11:31:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(review_portfolio.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/review-portfolio")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "432"
    assert cache_call.kwargs == {
        "feature": "review-portfolio",
        "input_fields": (
            "holdings",
            "risk_tolerance",
            "investment_goals",
            "target_market",
            "investment_horizon",
            "scenario",
        ),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached portfolio review" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    save_call = response.text.split("window.AlphaLabStorage.save(", maxsplit=1)[1].split(");", maxsplit=1)[0]
    for field in (
        "holdings",
        "risk_tolerance",
        "investment_goals",
        "target_market",
        "investment_horizon",
        "scenario",
    ):
        assert f"{field}:" in save_call
