import datetime
import json
import types
from decimal import Decimal
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import review_portfolio
from app.services import auth, portfolio_market_data
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
    prompt_task = types.SimpleNamespace(
        model="test-model",
        temperature=0.1,
        web_search=False,
        reasoning_level="low",
    )
    analyze_task = types.SimpleNamespace(
        model="test-model",
        temperature=0.1,
        web_search=True,
        reasoning_level="medium",
    )
    rebalance_task = types.SimpleNamespace(
        model="test-model",
        temperature=0.0,
        web_search=False,
        reasoning_level="high",
    )
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "REVIEW_PORTFOLIO_BUILD_PROMPT": prompt_task,
            "REVIEW_PORTFOLIO_ANALYZE": analyze_task,
            "REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT": prompt_task,
            "REVIEW_PORTFOLIO_REBALANCE_ANALYZE": rebalance_task,
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
    assert "approximate number of shares" in execute_prompt.await_args_list[0].args[2]


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
    get_cached_payload = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(review_portfolio.analysis_cache, "get_cached_result", get_cached_result)
    monkeypatch.setattr(review_portfolio.analysis_cache, "get_cached_payload", get_cached_payload)
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
    get_cached_payload.assert_awaited_once()
    payload_call = get_cached_payload.await_args
    assert payload_call.kwargs["feature"] == "review-portfolio-rebalance"
    assert payload_call.kwargs["input_fields"] == review_portfolio._REBALANCE_CACHE_INPUT_FIELDS
    assert payload_call.kwargs["payload_validator"] is review_portfolio.portfolio_rebalance.is_valid_cache_payload
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
    assert 'id="target-market" name="target_market"' in response.text
    assert 'required aria-required="true"' in response.text
    assert 'id="include-rebalance"' in response.text
    assert "REVIEW_PORTFOLIO_STORAGE_SCHEMA_VERSION = 2" in response.text
    assert "migrate: migrateReviewPortfolioStorage" in response.text
    assert "REVIEW_PORTFOLIO_MARKET_CURRENCIES" in response.text
    assert "marked/marked.min.js" in response.text
    assert "sanitizeMarkdownFragment" in response.text


def test_page_embeds_cached_rebalance_plan(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_rebalance = {
        "holdings": "AAPL, 10, 80",
        "risk_tolerance": "Moderate",
        "investment_goals": "Capital growth",
        "target_market": "US",
        "investment_horizon": "Long-term (3-5 years)",
        "scenario": "",
        "available_cash": "0",
        "allow_fractional_shares": "false",
        "minimum_trade_amount": "25",
        "tax_context": "taxable",
        "payload": {
            "content": "# Rebalance Plan",
            "plan": {
                "generated_at": "2026-07-23T01:03:00+00:00",
                "market_data_at": "2026-07-23T01:02:00+00:00",
                "market": "US",
                "currency": "USD",
                "trades": [],
            },
        },
        "generated_at": "2026-07-23T01:03:00+00:00",
    }
    get_cached_result = mock.AsyncMock(return_value=None)
    get_cached_payload = mock.AsyncMock(return_value=cached_rebalance)
    monkeypatch.setattr(review_portfolio.analysis_cache, "get_cached_result", get_cached_result)
    monkeypatch.setattr(review_portfolio.analysis_cache, "get_cached_payload", get_cached_payload)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/review-portfolio")

    assert response.status_code == 200
    assert 'id="cached-rebalance-data"' in response.text
    assert "# Rebalance Plan" in response.text
    assert 'id="export-rebalance-btn"' in response.text
    assert "portfolio-rebalance-" in response.text
    assert "safeCsvCell" in response.text


def _quote(ticker: str, price: str = "100") -> portfolio_market_data.MarketQuote:
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=ticker,
        price=Decimal(price),
        currency="USD",
        exchange="NMS",
        retrieved_at=datetime.datetime(2026, 7, 23, 1, 2, tzinfo=datetime.UTC),
    )


def _allocation_response() -> str:
    return json.dumps(
        {
            "strategy_summary": "Keep the portfolio concentrated in the validated core holding.",
            "allocations": [
                {
                    "ticker": "AAPL",
                    "target_weight_pct": 100,
                    "role": "Core",
                    "rationale": "Retain the existing quality exposure.",
                }
            ],
            "risks": ["Single-stock concentration remains high."],
            "execution_guidance": ["Verify the live quote before trading."],
            "tax_considerations": ["Review tax lots before any future sale."],
        }
    )


@pytest.mark.asyncio
async def test_rebalance_stream_emits_and_caches_structured_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated review prompt"),
            ai.AIResponse(success=True, completion="# Portfolio review"),
            ai.AIResponse(success=True, completion="Generated allocation prompt"),
            ai.AIResponse(success=True, completion=_allocation_response()),
        ]
    )
    fetch_quotes = mock.AsyncMock(return_value={"AAPL": _quote("AAPL")})
    set_cached_result = mock.AsyncMock(return_value=True)
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_portfolio.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_portfolio.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(review_portfolio.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(review_portfolio.analysis_cache, "set_cached_result", set_cached_result)
    monkeypatch.setattr(review_portfolio.analysis_cache, "set_cached_payload", set_cached_payload)

    response = await review_portfolio.review_portfolio_stream(
        Request({"type": "http", "method": "GET", "path": "/review-portfolio/stream", "headers": []}),
        holdings="AAPL, 10, 80",
        target_market="US",
        risk_tolerance="Moderate",
        investment_goals="Capital growth",
        investment_horizon="Long-term (3-5 years)",
        scenario="",
        include_rebalance=True,
        available_cash="0",
        allow_fractional_shares=False,
        minimum_trade_amount="25",
        tax_context="taxable",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    event_types = [event["type"] for event in events]
    assert "review_result" in event_types
    assert "rebalance_result" in event_types
    assert events[-1] == {"type": "complete", "status": "success"}
    rebalance_event = next(event for event in events if event["type"] == "rebalance_result")
    assert rebalance_event["plan"]["currency"] == "USD"
    assert rebalance_event["plan"]["trades"][0]["action"] == "HOLD"
    assert "Do not calculate exact trade quantities" in execute_prompt.await_args_list[0].args[2]
    assert "approximate number of shares" not in execute_prompt.await_args_list[0].args[2]
    assert execute_prompt.await_args_list[-1].kwargs["schema_name"] == "portfolio_rebalance_target"
    assert execute_prompt.await_args_list[-1].kwargs["enable_web_search"] is False
    set_cached_result.assert_awaited_once()
    set_cached_payload.assert_awaited_once()
    cache_call = set_cached_payload.await_args
    assert cache_call.kwargs["feature"] == "review-portfolio-rebalance"
    assert cache_call.kwargs["inputs"]["allow_fractional_shares"] == "false"
    assert cache_call.kwargs["payload"]["plan"]["currency"] == "USD"


@pytest.mark.asyncio
async def test_rebalance_failure_preserves_successful_review(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated review prompt"),
            ai.AIResponse(success=True, completion="# Portfolio review"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_portfolio.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_portfolio.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        review_portfolio.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(side_effect=portfolio_market_data.MarketDataError("Market data is unavailable for AAPL.")),
    )
    monkeypatch.setattr(review_portfolio.analysis_cache, "set_cached_result", set_cached_result)
    monkeypatch.setattr(review_portfolio.analysis_cache, "set_cached_payload", set_cached_payload)

    response = await review_portfolio.review_portfolio_stream(
        Request({"type": "http", "method": "GET", "path": "/review-portfolio/stream", "headers": []}),
        holdings="AAPL, 10",
        target_market="US",
        include_rebalance=True,
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert {"type": "review_result", "content": "# Portfolio review"} in events
    assert {
        "type": "rebalance_error",
        "message": "Market data is unavailable for AAPL.",
    } in events
    assert events[-1] == {"type": "complete", "status": "rebalance_failed"}
    set_cached_result.assert_awaited_once()
    set_cached_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_rebalance_holdings_stop_before_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock()
    monkeypatch.setattr(review_portfolio.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_portfolio.ai, "execute_prompt", execute_prompt)

    response = await review_portfolio.review_portfolio_stream(
        Request({"type": "http", "method": "GET", "path": "/review-portfolio/stream", "headers": []}),
        holdings="AAPL 10 shares",
        target_market="US",
        include_rebalance=True,
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "error"
    assert "Line 1" in events[-1]["message"]
    execute_prompt.assert_not_awaited()


def test_stream_requires_target_market(client: TestClient) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/review-portfolio/stream", params={"holdings": "AAPL 10 shares"})

    assert response.status_code == 422


def test_page_returns_service_error_without_supported_market(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review_portfolio.config.app_settings, "primary_markets", {"LSE"})
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/review-portfolio")

    assert response.status_code == 503
    assert response.text == "At least one supported primary market (US or AU) is required."
