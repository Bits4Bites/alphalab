import datetime
import json
import pathlib
import re
import shutil
import subprocess
import types
from decimal import Decimal
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import compare_investments
from app.schemas import investment_comparison as comparison_schemas
from app.services import auth, investment_comparison, portfolio_market_data
from app.utils import ai
from tests import test_investment_comparison as comparison_fixtures


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "compare-user",
        "email": "compare@example.com",
        "name": "Comparison User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "COMPARE_INVESTMENTS_BUILD_PROMPT": task,
            "COMPARE_INVESTMENTS_ANALYZE": task,
            "COMPARE_INVESTMENTS_ANALYZE_SCENARIO": task,
        },
    )


def _quote(ticker: str, *, asset_type: str = "stock") -> portfolio_market_data.MarketQuote:
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=ticker,
        price=Decimal("100"),
        currency="USD",
        exchange="NMS",
        retrieved_at=datetime.datetime(2020, 1, 2, 8, 0, tzinfo=datetime.UTC),
        asset_type=asset_type,
        display_name=f"{ticker} Investment",
    )


def _comparison_result() -> comparison_schemas.ComparisonResult:
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}
    research = investment_comparison.validate_research(
        comparison_fixtures._research(),
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=comparison_fixtures._market(),
        scenario="Recession",
    )
    return investment_comparison.build_result(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=comparison_fixtures._market(),
        scenario="Recession",
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_ranks_and_caches_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated comparison prompt"),
            ai.AIResponse(
                success=True,
                completion=json.dumps(comparison_fixtures._core_research_data()),
            ),
            ai.AIResponse(
                success=True,
                completion=json.dumps(comparison_fixtures._scenario_research_data()),
            ),
        ]
    )
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}
    fetch_quotes = mock.AsyncMock(return_value=quotes)
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(compare_investments.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(
        compare_investments.analysis_cache,
        "set_cached_payload",
        set_cached_payload,
    )

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers=" AAPL, QQQ ",
        target_market=" US ",
        scenario=" Recession ",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "result"
    assert [entry["ticker"] for entry in events[-1]["result"]["rankings"]] == ["QQQ", "AAPL"]
    assert events[-1]["result"]["scenario"] == "Recession"
    assert events[-2] == {
        "type": "progress",
        "step": 7,
        "total": 7,
        "message": "Comparison complete!",
    }
    fetch_quotes.assert_awaited_once()
    core_call = execute_prompt.await_args_list[1]
    assert core_call.kwargs["schema_name"] == "investment_comparison_core_research"
    assert core_call.kwargs["enable_web_search"] is True
    assert core_call.kwargs["response_json_schema"] == investment_comparison.core_research_schema()
    scenario_call = execute_prompt.await_args_list[2]
    assert scenario_call.kwargs["schema_name"] == "investment_comparison_scenario_research"
    assert scenario_call.kwargs["enable_web_search"] is True
    assert scenario_call.kwargs["response_json_schema"] == investment_comparison.scenario_research_schema()
    assert execute_prompt.await_args_list[0].kwargs["enable_web_search"] is False
    assert "Recession" not in execute_prompt.await_args_list[0].args[2]
    assert "Recession" not in core_call.args[2]
    assert "Recession" in scenario_call.args[2]
    set_cached_payload.assert_awaited_once()
    cache_call = set_cached_payload.await_args
    assert cache_call.kwargs["feature"] == "compare-investments"
    assert cache_call.kwargs["inputs"] == {
        "tickers": "AAPL, QQQ",
        "target_market": "US",
        "scenario": "Recession",
    }
    assert cache_call.kwargs["payload"]["result"]["rankings"][0]["ticker"] == "QQQ"


@pytest.mark.asyncio
async def test_invalid_candidates_stop_before_market_data_or_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_prompt = mock.AsyncMock()
    fetch_quotes = mock.AsyncMock()
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(compare_investments.portfolio_market_data, "fetch_quotes", fetch_quotes)

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers="AAPL, AAPL",
        target_market="US",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1] == {"type": "error", "message": "Ticker AAPL is listed more than once."}
    fetch_quotes.assert_not_awaited()
    execute_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_data_failure_stops_before_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock()
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        compare_investments.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(side_effect=portfolio_market_data.MarketDataError("No quote for QQQ.")),
    )

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers="AAPL, QQQ",
        target_market="US",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1] == {"type": "error", "message": "No quote for QQQ."}
    execute_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_ai_response_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated comparison prompt"),
            ai.AIResponse(success=True, completion="not json"),
        ]
    )
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        compare_investments.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(
            return_value={
                "AAPL": _quote("AAPL"),
                "QQQ": _quote("QQQ", asset_type="etf"),
            }
        ),
    )
    monkeypatch.setattr(
        compare_investments.analysis_cache,
        "set_cached_payload",
        set_cached_payload,
    )

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers="AAPL, QQQ",
        target_market="US",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1] == {
        "type": "error",
        "message": "The AI returned an invalid comparison response.",
    }
    set_cached_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_response_cannot_inject_scenario_or_be_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated comparison prompt"),
            ai.AIResponse(
                success=True,
                completion=json.dumps(comparison_fixtures._research_data()),
            ),
        ]
    )
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        compare_investments.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(
            return_value={
                "AAPL": _quote("AAPL"),
                "QQQ": _quote("QQQ", asset_type="etf"),
            }
        ),
    )
    monkeypatch.setattr(
        compare_investments.analysis_cache,
        "set_cached_payload",
        set_cached_payload,
    )

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers="AAPL, QQQ",
        target_market="US",
        scenario="",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1] == {
        "type": "error",
        "message": "The AI comparison failed structured validation.",
    }
    set_cached_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_unassessed_scenario_response_is_not_emitted_or_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated comparison prompt"),
            ai.AIResponse(
                success=True,
                completion=json.dumps(comparison_fixtures._core_research_data()),
            ),
            ai.AIResponse(
                success=True,
                completion=json.dumps(comparison_fixtures._scenario_research_data(assessed=False)),
            ),
        ]
    )
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        compare_investments.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(
            return_value={
                "AAPL": _quote("AAPL"),
                "QQQ": _quote("QQQ", asset_type="etf"),
            }
        ),
    )
    monkeypatch.setattr(
        compare_investments.analysis_cache,
        "set_cached_payload",
        set_cached_payload,
    )

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers="AAPL, QQQ",
        target_market="US",
        scenario="Recession",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1] == {
        "type": "error",
        "message": "The AI scenario assessments do not match the comparison request.",
    }
    set_cached_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_without_scenario_skips_scenario_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated comparison prompt"),
            ai.AIResponse(
                success=True,
                completion=json.dumps(comparison_fixtures._core_research_data()),
            ),
        ]
    )
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(compare_investments.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(compare_investments.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(
        compare_investments.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(
            return_value={
                "AAPL": _quote("AAPL"),
                "QQQ": _quote("QQQ", asset_type="etf"),
            }
        ),
    )
    monkeypatch.setattr(
        compare_investments.analysis_cache,
        "set_cached_payload",
        set_cached_payload,
    )

    response = await compare_investments.compare_investments_stream(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/compare-investments/stream",
                "headers": [],
            }
        ),
        tickers="AAPL, QQQ",
        target_market="US",
        scenario="",
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert len(execute_prompt.await_args_list) == 2
    assert events[-2] == {
        "type": "progress",
        "step": 6,
        "total": 6,
        "message": "Comparison complete!",
    }
    assert events[-1]["type"] == "result"
    assert all(candidate["scenario"]["impact"] == "not_assessed" for candidate in events[-1]["result"]["candidates"])
    set_cached_payload.assert_awaited_once()


def test_page_redirects_when_unauthenticated(client: TestClient) -> None:
    response = client.get("/compare-investments", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_page_restores_cached_structured_comparison(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _comparison_result()
    cached_result = {
        "tickers": "AAPL, QQQ",
        "target_market": "US",
        "scenario": "Recession",
        "payload": investment_comparison.cache_payload(result),
        "generated_at": "2020-01-02T09:00:00+00:00",
    }
    get_cached_payload = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(
        compare_investments.analysis_cache,
        "get_cached_payload",
        get_cached_payload,
    )
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/compare-investments")

    assert response.status_code == 200
    cache_call = get_cached_payload.await_args
    assert cache_call.kwargs["feature"] == "compare-investments"
    assert cache_call.kwargs["input_fields"] == ("tickers", "target_market", "scenario")
    assert cache_call.kwargs["payload_validator"] is compare_investments.investment_comparison.is_valid_cache_payload
    assert "Compare stocks and ETFs" in response.text
    assert 'id="comparison-form"' in response.text
    assert 'id="cached-result-data"' in response.text
    assert "AAPL, QQQ" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    assert "investment-comparison-" in response.text
    assert "safeCsvCell" in response.text
    assert "return Number(value).toFixed(2);" in response.text


def test_score_formatter_keeps_close_scores_visually_distinct() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the rendered score-format regression test.")

    template = pathlib.Path("app/templates/compare_investments.html").read_text(encoding="utf-8")
    formatter = re.search(
        r"function formatScore\(value\) \{\s+return Number\(value\)\.toFixed\(\d+\);\s+\}",
        template,
    )
    assert formatter is not None
    script = f"{formatter.group(0)}\nprocess.stdout.write(JSON.stringify([formatScore(69.95), formatScore(70)]));"

    completed = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == ["69.95", "70.00"]


def test_stream_requires_target_market(client: TestClient) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get(
        "/compare-investments/stream",
        params={"tickers": "AAPL, QQQ"},
    )

    assert response.status_code == 422
