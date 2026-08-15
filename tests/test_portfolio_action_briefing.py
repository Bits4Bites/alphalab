import datetime
import json
import types
from decimal import Decimal
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse

from app.routers import portfolio_action_briefing
from app.schemas import portfolio_action_briefing as briefing_schemas
from app.services import auth, portfolio_market_data
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "briefing-user",
        "email": "briefing@example.com",
        "name": "Briefing User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    prompt_task = types.SimpleNamespace(
        model="test-model",
        temperature=0.1,
        web_search=False,
        reasoning_level="low",
    )
    analyze_task = types.SimpleNamespace(
        model="test-model",
        temperature=0.0,
        web_search=True,
        reasoning_level="high",
    )
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "PORTFOLIO_ACTION_BRIEFING_BUILD_PROMPT": prompt_task,
            "PORTFOLIO_ACTION_BRIEFING_ANALYZE": analyze_task,
        },
    )


def _quote(ticker: str, price: str) -> portfolio_market_data.MarketQuote:
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=ticker,
        price=Decimal(price),
        currency="USD",
        exchange="NMS",
        retrieved_at=datetime.datetime(2026, 8, 7, 3, 0, tzinfo=datetime.UTC),
        display_name=ticker,
    )


def _research() -> dict[str, object]:
    return {
        "as_of": "2026-08-07T03:05:00Z",
        "headline": "Concentrated technology exposure needs attention before earnings.",
        "overall_stance": "Cautious",
        "confidence": "high",
        "actions": [
            {
                "ticker": "AAPL",
                "action": "TRIM",
                "urgency": "today",
                "impact": "high",
                "confidence": "high",
                "rationale": "The position is concentrated ahead of a material event.",
                "sizing_pct": 10,
                "source_ids": ["s1"],
            },
            {
                "ticker": "MSFT",
                "action": "WATCH",
                "urgency": "this_week",
                "impact": "medium",
                "confidence": "medium",
                "rationale": "Wait for confirmed guidance before deploying cash.",
                "sizing_pct": None,
                "source_ids": ["s1"],
            },
        ],
        "risks": ["Technology concentration remains elevated."],
        "upcoming_events": [
            {
                "date": "2026-08-10",
                "ticker": "AAPL",
                "title": "Investor update",
                "description": "Management is expected to discuss product demand.",
                "source_ids": ["s1"],
            }
        ],
        "sources": [
            {
                "id": "s1",
                "title": "Company investor relations update",
                "publisher": "Example Publisher",
                "url": "https://example.com/update",
                "published_at": "2026-08-07",
            }
        ],
        "warnings": ["Quotes are delayed."],
    }


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


def test_page_renders_stateless_briefing_ui(client: TestClient) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/portfolio-action-briefing")

    assert response.status_code == 200
    assert 'id="briefing-form"' in response.text
    assert 'id="import-review-btn"' in response.text
    assert "method: 'POST'" in response.text
    assert "portfolio-action-briefing" in response.text
    assert "AlphaLabStorage.save" in response.text
    for value, label in (
        ("today", "Today"),
        ("7", "Next 7 days"),
        ("14", "Next 2 weeks"),
        ("30", "Next month"),
        ("90", "Next 3 months"),
    ):
        assert f'<option value="{value}"' in response.text
        assert label in response.text


@pytest.mark.asyncio
async def test_stream_validates_quotes_and_returns_ranked_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated premium briefing prompt"),
            ai.AIResponse(success=True, completion=json.dumps(_research())),
        ]
    )
    fetch_quotes = mock.AsyncMock(
        side_effect=[
            {"AAPL": _quote("AAPL", "200")},
            {"MSFT": _quote("MSFT", "400")},
        ]
    )
    monkeypatch.setattr(portfolio_action_briefing.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_action_briefing.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(portfolio_action_briefing.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(
        portfolio_action_briefing.portfolio_action_briefing.datetime,
        "datetime",
        mock.Mock(
            wraps=datetime.datetime,
            now=mock.Mock(return_value=datetime.datetime(2026, 8, 7, 3, 10, tzinfo=datetime.UTC)),
        ),
    )
    body = briefing_schemas.BriefingRequest(
        holdings="AAPL, 10, 150",
        target_market="US",
        watchlist="MSFT",
        horizon="7",
        risk_tolerance="Moderate",
        available_cash="1000",
    )

    response = await portfolio_action_briefing.portfolio_action_briefing_stream(body, _user())
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "result"
    payload = events[-1]["payload"]
    assert payload["summary"]["portfolio_value"] == 3000.0
    assert payload["summary"]["priority_actions_count"] == 1
    assert [action["ticker"] for action in payload["actions"]] == ["AAPL", "MSFT"]
    assert payload["actions"][0]["suggested_quantity"] == 1.0
    assert payload["actions"][0]["estimated_value"] == 200.0
    assert payload["upcoming_events"][0]["date"] == "2026-08-10"
    assert execute_prompt.await_args_list[0].kwargs["enable_web_search"] is False
    assert execute_prompt.await_args_list[1].kwargs["enable_web_search"] is True
    assert execute_prompt.await_args_list[1].kwargs["response_json_schema"]


@pytest.mark.asyncio
async def test_stream_rejects_research_for_unsubmitted_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    research = _research()
    research["actions"] = [
        {
            **research["actions"][0],
            "ticker": "NVDA",
        }
    ]
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated prompt"),
            ai.AIResponse(success=True, completion=json.dumps(research)),
            ai.AIResponse(success=True, completion=json.dumps(research)),
        ]
    )
    monkeypatch.setattr(portfolio_action_briefing.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_action_briefing.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        portfolio_action_briefing.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(side_effect=[{"AAPL": _quote("AAPL", "200")}, {"MSFT": _quote("MSFT", "400")}]),
    )
    body = briefing_schemas.BriefingRequest(
        holdings="AAPL, 10",
        target_market="US",
        watchlist="MSFT",
        horizon="7",
    )

    response = await portfolio_action_briefing.portfolio_action_briefing_stream(body, _user())
    events = await _collect_stream_events(response)

    assert events[-1] == {
        "type": "error",
        "message": "The AI action briefing remained invalid after one repair attempt.",
    }


@pytest.mark.asyncio
async def test_stream_repairs_invalid_structured_research(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_research = _research()
    invalid_research.pop("headline")
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated prompt"),
            ai.AIResponse(success=True, completion=json.dumps(invalid_research)),
            ai.AIResponse(success=True, completion=json.dumps(_research())),
        ]
    )
    monkeypatch.setattr(portfolio_action_briefing.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_action_briefing.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        portfolio_action_briefing.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(side_effect=[{"AAPL": _quote("AAPL", "200")}, {"MSFT": _quote("MSFT", "400")}]),
    )
    body = briefing_schemas.BriefingRequest(
        holdings="AAPL, 10",
        target_market="US",
        watchlist="MSFT",
        horizon="90",
    )

    response = await portfolio_action_briefing.portfolio_action_briefing_stream(body, _user())
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "result"
    assert any(event.get("message") == "Repairing the briefing response..." for event in events)
    repair_call = execute_prompt.await_args_list[2]
    assert repair_call.kwargs["enable_web_search"] is False
    assert repair_call.kwargs["schema_name"] == "portfolio_action_briefing_repair"
    assert "headline: Field required" in repair_call.args[2]
