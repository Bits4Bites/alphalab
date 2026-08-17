import copy
import datetime
import json
import types
from decimal import Decimal
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sse_starlette import sse

from app import config
from app.routers import build_portfolio as build_portfolio_router
from app.schemas import build_portfolio as build_portfolio_schemas
from app.services import auth, build_portfolio, portfolio_market_data
from app.utils import ai

TODAY = datetime.date.today()
ADAPTIVE_PROMPT = (
    "Research a diversified portfolio aligned with the validated intent, using current evidence for every target "
    "allocation and clearly disclosing assumptions, concentration, liquidity, and market risks."
)


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "321",
        "email": "builder@example.com",
        "name": "Portfolio Builder",
        "avatar": "",
    }


def _market(code: str = "US") -> portfolio_market_data.MarketDefinition:
    market = portfolio_market_data.resolve_market(code)
    assert market is not None
    return market


def _request(
    *,
    budget: str = "$10,000",
    allow_fractional_shares: bool = False,
    existing_holdings: str = "",
) -> build_portfolio_schemas.BuildPortfolioRequest:
    return build_portfolio_schemas.BuildPortfolioRequest(
        risk_tolerance="Moderate",
        portfolio_intent="Build a diversified long-term ETF portfolio balancing growth and income.",
        target_market="US",
        investment_horizon="Long-term (3-5 years)",
        budget=budget,
        allow_fractional_shares=allow_fractional_shares,
        existing_holdings=existing_holdings,
    )


def _task_settings() -> types.SimpleNamespace:
    prompt_client = object()
    research_client = object()
    prompt_task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="LowCost",
        model="gpt-5.6-luna",
        web_search=False,
        reasoning_level="low",
    )
    research_task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="Premium",
        model="gpt-5.6-terra",
        web_search=True,
        reasoning_level="medium",
    )
    clients = {
        "BUILD_PORTFOLIO_BUILD_PROMPT": prompt_client,
        "BUILD_PORTFOLIO_ANALYZE": research_client,
    }
    return types.SimpleNamespace(
        prompt_client=prompt_client,
        research_client=research_client,
        prompt_task=prompt_task,
        research_task=research_task,
        get_ai_client=lambda task_id: clients.get(task_id),
        tasks={
            "BUILD_PORTFOLIO_BUILD_PROMPT": prompt_task,
            "BUILD_PORTFOLIO_ANALYZE": research_task,
        },
    )


def _research_data(
    *,
    market: str = "US",
    strategy_summary: str = "Use broad equity and bond ETFs with a strategic cash reserve.",
) -> dict[str, object]:
    return {
        "as_of": TODAY.isoformat(),
        "market": market,
        "strategy_summary": strategy_summary,
        "allocations": [
            {
                "ticker": "VTI",
                "target_weight_pct": 60,
                "role": "Core growth",
                "rationale": "Provides diversified exposure to the United States equity market.",
                "source_ids": ["S1"],
            },
            {
                "ticker": "BND",
                "target_weight_pct": 30,
                "role": "Defensive income",
                "rationale": "Adds diversified investment-grade bond exposure and moderates equity risk.",
                "source_ids": ["S2"],
            },
            {
                "ticker": "CASH",
                "target_weight_pct": 10,
                "role": "Liquidity reserve",
                "rationale": "Preserves liquidity for staged deployment and portfolio expenses.",
                "source_ids": ["S2"],
            },
        ],
        "portfolio_risks": [
            {
                "statement": "Equity valuations and interest-rate changes can reduce portfolio value.",
                "source_ids": ["S1", "S2"],
            }
        ],
        "assumptions": ["The investor accepts United States dollar exposure."],
        "execution_guidance": [
            {
                "statement": "Confirm live prices and transaction costs before placing orders.",
                "source_ids": ["S1"],
            }
        ],
        "tax_considerations": [
            {
                "statement": "Account-specific tax treatment requires independent verification.",
                "source_ids": ["S2"],
            }
        ],
        "sources": [
            {
                "id": "S1",
                "title": "Vanguard Total Stock Market ETF",
                "publisher": "Vanguard",
                "published_at": (TODAY - datetime.timedelta(days=2)).isoformat(),
                "url": "https://example.com/vti",
            },
            {
                "id": "S2",
                "title": "Vanguard Total Bond Market ETF",
                "publisher": "Vanguard",
                "published_at": (TODAY - datetime.timedelta(days=3)).isoformat(),
                "url": "https://example.com/bnd",
            },
        ],
    }


def _quote(
    ticker: str,
    price: str,
    *,
    asset_type: str = "etf",
    sector: str = "Diversified ETF",
) -> portfolio_market_data.MarketQuote:
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=ticker,
        price=Decimal(price),
        currency="USD",
        exchange="NMS",
        retrieved_at=datetime.datetime.now(datetime.UTC),
        asset_type=asset_type,
        display_name=f"{ticker} Fund",
        market_cap=Decimal("250000000000"),
        average_volume=2_000_000,
        sector=sector,
    )


def _recommendation_quotes() -> dict[str, portfolio_market_data.MarketQuote]:
    return {
        "VTI": _quote("VTI", "250", sector="Broad equity"),
        "BND": _quote("BND", "75", sector="Fixed income"),
    }


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


def _payload_data(
    *,
    strategy_summary: str = "Use broad equity and bond ETFs with a strategic cash reserve.",
) -> dict[str, object]:
    request = _request()
    budget = build_portfolio.parse_budget(request.budget, _market())
    report = build_portfolio.parse_research(
        json.dumps(_research_data(strategy_summary=strategy_summary)),
        _market(),
    )
    payload = build_portfolio.build_payload(
        request,
        _market(),
        budget,
        report,
        _recommendation_quotes(),
        [],
        {},
    )
    return payload.model_dump(mode="json")


def test_request_schema_and_budget_modes() -> None:
    request = _request(budget="$1,000 monthly")
    assert request.allow_fractional_shares is False

    total = build_portfolio.parse_budget("$10,000", _market("AU"))
    monthly = build_portfolio.parse_budget("$1,000 monthly", _market())
    weekly = build_portfolio.parse_budget("USD 250 /week", _market())

    assert total is not None
    assert (total.amount, total.currency, total.cadence) == (Decimal("10000"), "AUD", "total")
    assert total.label == "AUD 10,000 total budget"
    assert monthly is not None
    assert (monthly.amount, monthly.currency, monthly.cadence) == (Decimal("1000"), "USD", "monthly")
    assert weekly is not None
    assert weekly.cadence == "weekly"
    assert build_portfolio.parse_budget("", _market()) is None

    for invalid in ("AUD 1000", "$0", "$1,00", "$1000 whenever"):
        with pytest.raises(build_portfolio.BudgetInputError):
            build_portfolio.parse_budget(invalid, _market())

    for invalid in (
        {"risk_tolerance": "Extreme", "portfolio_intent": "Growth", "target_market": "US"},
        {"risk_tolerance": "Moderate", "portfolio_intent": "x\u0000", "target_market": "US"},
        {"risk_tolerance": "Moderate", "portfolio_intent": "Growth", "target_market": "US\nAU"},
        {
            "risk_tolerance": "Moderate",
            "portfolio_intent": "Growth",
            "target_market": "US",
            "unexpected": True,
        },
    ):
        with pytest.raises(ValidationError):
            build_portfolio_schemas.BuildPortfolioRequest.model_validate(invalid)


def test_research_schema_is_strict_and_semantically_validated() -> None:
    schema = build_portfolio.response_schema()
    schema_json = json.dumps(schema)
    assert '"format": "uri"' not in schema_json

    pending: list[object] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(properties)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)

    report = build_portfolio.parse_research(json.dumps(_research_data()), _market())
    assert [allocation.ticker for allocation in report.allocations] == ["VTI", "BND", "CASH"]

    wrong_market = _research_data(market="AU")
    with pytest.raises(build_portfolio.ResearchReportError):
        build_portfolio.parse_research(json.dumps(wrong_market), _market())

    stale = copy.deepcopy(_research_data())
    stale["sources"][0]["published_at"] = (TODAY - datetime.timedelta(days=181)).isoformat()  # type: ignore[index]
    with pytest.raises(build_portfolio.ResearchReportError):
        build_portfolio.parse_research(json.dumps(stale), _market())

    unsafe_url = copy.deepcopy(_research_data())
    unsafe_url["sources"][0]["url"] = "javascript:alert(1)"  # type: ignore[index]
    with pytest.raises(build_portfolio.ResearchReportError):
        build_portfolio.parse_research(json.dumps(unsafe_url), _market())


def test_deterministic_sizing_handles_total_and_monthly_budgets() -> None:
    report = build_portfolio.parse_research(json.dumps(_research_data()), _market())
    total_request = _request()
    total_payload = build_portfolio.build_payload(
        total_request,
        _market(),
        build_portfolio.parse_budget(total_request.budget, _market()),
        report,
        _recommendation_quotes(),
        [],
        {},
    )

    allocations = {allocation.ticker: allocation for allocation in total_payload.allocations}
    assert allocations["VTI"].quantity == 24
    assert allocations["VTI"].estimated_cost == 6000
    assert allocations["BND"].quantity == 40
    assert allocations["BND"].estimated_cost == 3000
    assert total_payload.residual_cash == 1000
    assert total_payload.quality.security_count == 2
    assert total_payload.sector_exposures[0].target_weight_pct == 60

    monthly_request = _request(budget="$1,000 monthly", allow_fractional_shares=True)
    monthly_payload = build_portfolio.build_payload(
        monthly_request,
        _market(),
        build_portfolio.parse_budget(monthly_request.budget, _market()),
        report,
        _recommendation_quotes(),
        [],
        {},
    )
    monthly_allocations = {allocation.ticker: allocation for allocation in monthly_payload.allocations}
    assert monthly_payload.budget is not None
    assert monthly_payload.budget.cadence == "monthly"
    assert monthly_allocations["VTI"].quantity == 2.4
    assert monthly_allocations["BND"].quantity == 4
    assert monthly_payload.residual_cash == 100
    assert any("each monthly contribution" in warning for warning in monthly_payload.warnings)

    existing_vti = build_portfolio_schemas.VerifiedHolding(
        ticker="VTI",
        display_name="VTI Fund",
        quantity=2,
        current_price=250,
        market_value=500,
        average_cost=200,
    )
    adjusted_payload = build_portfolio.build_payload(
        total_request,
        _market(),
        build_portfolio.parse_budget(total_request.budget, _market()),
        report,
        _recommendation_quotes(),
        [existing_vti],
        {"VTI": _recommendation_quotes()["VTI"]},
    )
    adjusted = {allocation.ticker: allocation for allocation in adjusted_payload.allocations}
    assert adjusted["VTI"].target_value == 5800
    assert adjusted["BND"].target_value == 3150
    assert adjusted["CASH"].target_value == 1050
    assert any("combined portfolio toward target weights" in warning for warning in adjusted_payload.warnings)


@pytest.mark.asyncio
async def test_successful_stream_uses_two_models_verifies_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    unsafe_summary = '<script>alert("portfolio")</script>'
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data(strategy_summary=unsafe_summary))),
        ]
    )
    fetch_quotes = mock.AsyncMock(return_value=_recommendation_quotes())
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(build_portfolio_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU", "VN"})
    monkeypatch.setattr(build_portfolio_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(build_portfolio_router.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(build_portfolio_router.analysis_cache, "set_cached_payload", set_cached_payload)
    request = _request()

    response = await build_portfolio_router.build_portfolio_stream(request, _user())
    events = await _collect_stream_events(response)

    assert execute.await_count == 2
    prompt_call, research_call = execute.await_args_list
    assert prompt_call.args[:2] == (settings.prompt_client, settings.prompt_task)
    assert "Act only as a prompt writer" in prompt_call.args[2]
    assert prompt_call.kwargs == {}
    assert research_call.args[:2] == (settings.research_client, settings.research_task)
    assert "Trusted server-owned role" in research_call.args[2]
    assert research_call.kwargs == {
        "response_json_schema": build_portfolio.response_schema(),
        "schema_name": "build_portfolio_research",
    }
    fetch_quotes.assert_awaited_once_with(("VTI", "BND"), _market())
    set_cached_payload.assert_awaited_once()
    cache_call = set_cached_payload.await_args
    assert cache_call.kwargs["feature"] == "build-portfolio"
    assert cache_call.kwargs["inputs"]["portfolio_intent"] == request.portfolio_intent
    assert cache_call.kwargs["inputs"]["allow_fractional_shares"] == "false"
    assert cache_call.kwargs["ttl_seconds"] == 72 * 60 * 60
    assert events[-1]["type"] == "result"
    assert unsafe_summary not in events[-1]["html"]
    assert "&lt;script&gt;alert" in events[-1]["html"]


@pytest.mark.asyncio
async def test_invalid_research_gets_one_conditional_terra_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data(market="AU"))),
            ai.AIResponse(completion=json.dumps(_research_data())),
        ]
    )
    fetch_quotes = mock.AsyncMock(return_value=_recommendation_quotes())
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(build_portfolio_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU", "VN"})
    monkeypatch.setattr(build_portfolio_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(build_portfolio_router.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(build_portfolio_router.analysis_cache, "set_cached_payload", set_cached_payload)

    response = await build_portfolio_router.build_portfolio_stream(_request(), _user())
    events = await _collect_stream_events(response)

    assert execute.await_count == 3
    correction_call = execute.await_args_list[2]
    assert correction_call.args[:2] == (settings.research_client, settings.research_task)
    assert "failed application validation" in correction_call.args[2]
    assert "does not match the selected market" in correction_call.args[2]
    assert correction_call.kwargs["response_json_schema"] == build_portfolio.response_schema()
    fetch_quotes.assert_awaited_once()
    set_cached_payload.assert_awaited_once()
    assert events[-1]["type"] == "result"


@pytest.mark.asyncio
async def test_second_invalid_research_fails_without_more_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data(market="AU"))),
            ai.AIResponse(completion=json.dumps(_research_data(market="AU"))),
        ]
    )
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(build_portfolio_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU"})
    monkeypatch.setattr(build_portfolio_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(
        build_portfolio_router.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(return_value=_recommendation_quotes()),
    )
    monkeypatch.setattr(build_portfolio_router.analysis_cache, "set_cached_payload", set_cached_payload)

    response = await build_portfolio_router.build_portfolio_stream(_request(), _user())
    events = await _collect_stream_events(response)

    assert execute.await_count == 3
    assert events[-1] == {
        "type": "error",
        "message": "Portfolio recommendations could not be verified. Please try again.",
    }
    set_cached_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_holdings_are_verified_before_prompt_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data())),
        ]
    )
    holding_quote = _quote("AAPL", "200", asset_type="stock", sector="Technology")
    fetch_quotes = mock.AsyncMock(
        side_effect=[
            {"AAPL": holding_quote},
            _recommendation_quotes(),
        ]
    )
    monkeypatch.setattr(build_portfolio_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU"})
    monkeypatch.setattr(build_portfolio_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(build_portfolio_router.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(
        build_portfolio_router.analysis_cache,
        "set_cached_payload",
        mock.AsyncMock(return_value=True),
    )

    response = await build_portfolio_router.build_portfolio_stream(
        _request(existing_holdings="AAPL, 2, 150"),
        _user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "result"
    assert fetch_quotes.await_args_list[0].args == (("AAPL",), _market())
    writer_prompt = execute.await_args_list[0].args[2]
    assert '"ticker": "AAPL"' in writer_prompt
    assert '"market_value": 400.0' in writer_prompt


@pytest.mark.asyncio
async def test_provider_failure_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(success=False, error="secret provider endpoint"),
        ]
    )
    monkeypatch.setattr(build_portfolio_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU"})
    monkeypatch.setattr(build_portfolio_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(
        build_portfolio_router.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(return_value=_recommendation_quotes()),
    )
    set_cached_payload = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(build_portfolio_router.analysis_cache, "set_cached_payload", set_cached_payload)

    response = await build_portfolio_router.build_portfolio_stream(_request(), _user())
    events = await _collect_stream_events(response)

    assert events[-1] == {"type": "error", "message": "Portfolio research failed. Please try again."}
    assert "secret provider" not in json.dumps(events)
    set_cached_payload.assert_not_awaited()


def test_page_renders_cached_payload_and_post_storage_contract(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe_summary = '<img src=x onerror="alert(1)">'
    cached_result = {
        "risk_tolerance": "Moderate",
        "portfolio_intent": "Balanced ETF portfolio",
        "target_market": "US",
        "investment_horizon": "Long-term (3-5 years)",
        "budget": "$10,000",
        "allow_fractional_shares": "false",
        "existing_holdings": "",
        "payload": _payload_data(strategy_summary=unsafe_summary),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    get_cached_payload = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(build_portfolio_router.analysis_cache, "get_cached_payload", get_cached_payload)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU", "VN"})
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/build-portfolio")

    assert response.status_code == 200
    get_cached_payload.assert_awaited_once()
    cache_call = get_cached_payload.await_args
    assert cache_call.kwargs["feature"] == "build-portfolio"
    assert cache_call.kwargs["input_fields"] == (
        "risk_tolerance",
        "portfolio_intent",
        "target_market",
        "investment_horizon",
        "budget",
        "allow_fractional_shares",
        "existing_holdings",
    )
    assert cache_call.kwargs["payload_validator"] is build_portfolio.is_valid_cache_payload
    assert unsafe_summary not in response.text
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in response.text
    assert 'name="portfolio_intent"' in response.text
    assert 'name="allow_fractional_shares"' in response.text
    assert "$10,000 or $1,000 monthly" in response.text
    assert "BUILD_PORTFOLIO_STORAGE_SCHEMA_VERSION = 2" in response.text
    assert "fetch('/build-portfolio/stream'" in response.text
    assert "method: 'POST'" in response.text
    assert "new EventSource" not in response.text
    assert "marked.min.js" not in response.text
    assert "renderMarkdown" not in response.text
    assert "result-section').classList.add('d-none')" in response.text
    assert 'data-portfolio-intent-handoff-target="build"' in response.text
    assert "United States (USD)" in response.text
    assert "Australia (AUD)" in response.text
    assert "Vietnam (VND)" in response.text


def test_page_market_options_derive_from_primary_markets(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        build_portfolio_router.analysis_cache,
        "get_cached_payload",
        mock.AsyncMock(return_value=None),
    )
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"AU", "VN"})
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/build-portfolio")

    assert response.status_code == 200
    assert "Australia (AUD)" in response.text
    assert "Vietnam (VND)" in response.text
    assert "United States (USD)" not in response.text


def test_endpoint_is_post_only_and_validates_body(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data())),
        ]
    )
    monkeypatch.setattr(build_portfolio_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU"})
    monkeypatch.setattr(build_portfolio_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(
        build_portfolio_router.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(return_value=_recommendation_quotes()),
    )
    monkeypatch.setattr(
        build_portfolio_router.analysis_cache,
        "set_cached_payload",
        mock.AsyncMock(return_value=True),
    )
    client.cookies.set("access_token", auth.create_access_token(_user()))

    get_response = client.get("/build-portfolio/stream")
    post_response = client.post("/build-portfolio/stream", json=_request().model_dump())
    invalid_response = client.post(
        "/build-portfolio/stream",
        json={"risk_tolerance": "Extreme", "portfolio_intent": "Growth", "target_market": "US"},
    )

    assert get_response.status_code == 405
    assert post_response.status_code == 200
    assert post_response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "result"' in post_response.text
    assert invalid_response.status_code == 422
    assert execute.await_count == 2


def test_cache_and_task_policy() -> None:
    payload = _payload_data()
    assert build_portfolio.is_valid_cache_payload(payload) is True

    stale = copy.deepcopy(payload)
    stale["generated_at"] = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=74)).isoformat()
    assert build_portfolio.is_valid_cache_payload(stale) is False

    prompt_task = config.ai_task_settings.tasks["BUILD_PORTFOLIO_BUILD_PROMPT"]
    research_task = config.ai_task_settings.tasks["BUILD_PORTFOLIO_ANALYZE"]
    assert prompt_task.model == "gpt-5.6-luna"
    assert prompt_task.web_search is False
    assert prompt_task.reasoning_level == "low"
    assert research_task.model == "gpt-5.6-terra"
    assert research_task.web_search is True
    assert research_task.reasoning_level == "medium"
