import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import dividend_event
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "654",
        "email": "dividend@example.com",
        "name": "Dividend User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1, web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "DIVIDEND_EVENT_BUILD_PROMPT": task,
            "DIVIDEND_EVENT_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_analysis_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated analysis prompt"),
            ai.AIResponse(success=True, completion="# Dividend analysis"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(dividend_event.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(dividend_event.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(dividend_event.analysis_cache, "set_cached_result", set_cached_result)

    response = await dividend_event.dividend_event_stream(
        Request({"type": "http", "method": "GET", "path": "/dividend-event/stream", "headers": []}),
        ticker=" aapl ",
        dividend_amount=" $0.25 ",
        ex_dividend_date=" 2026-08-07 ",
        current_price=" $215.00 ",
        holding_period=" Short-term (days to weeks) ",
        tax_bracket=" Low tax bracket ",
        additional_notes=" Already hold 100 shares ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="dividend-event",
        inputs={
            "ticker": "AAPL",
            "dividend_amount": "$0.25",
            "ex_dividend_date": "2026-08-07",
            "current_price": "$215.00",
            "holding_period": "Short-term (days to weeks)",
            "tax_bracket": "Low tax bracket",
            "additional_notes": "Already hold 100 shares",
        },
        content="# Dividend analysis",
    )
    assert events[-1] == {"type": "result", "content": "# Dividend analysis"}


def test_page_embeds_cached_result_and_browser_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "ticker": "AAPL",
        "dividend_amount": "$0.25",
        "ex_dividend_date": "2026-08-07",
        "current_price": "$215.00",
        "holding_period": "Short-term (days to weeks)",
        "tax_bracket": "Low tax bracket",
        "additional_notes": "Already hold 100 shares",
        "content": "# Cached dividend analysis",
        "generated_at": "2026-07-22T11:05:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(dividend_event.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/dividend-event")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "654"
    assert cache_call.kwargs == {
        "feature": "dividend-event",
        "input_fields": (
            "ticker",
            "dividend_amount",
            "ex_dividend_date",
            "current_price",
            "holding_period",
            "tax_bracket",
            "additional_notes",
        ),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached dividend analysis" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    save_call = response.text.split("window.AlphaLabStorage.save(", maxsplit=1)[1].split(");", maxsplit=1)[0]
    for field in (
        "ticker",
        "dividend_amount",
        "ex_dividend_date",
        "current_price",
        "holding_period",
        "tax_bracket",
        "additional_notes",
    ):
        assert f"{field}:" in save_call
