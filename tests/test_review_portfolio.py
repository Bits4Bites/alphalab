import copy
import datetime
import json
import pathlib
import types
from decimal import Decimal
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sse_starlette import sse

from app.routers import review_portfolio as review_router
from app.schemas import review_portfolio as review_schemas
from app.services import (
    auth,
    build_portfolio,
    portfolio_market_data,
    portfolio_rebalance,
    review_portfolio,
)
from app.utils import ai

TODAY = datetime.date.today()
ADAPTIVE_REVIEW_PROMPT = (
    "Diagnose the validated portfolio using current evidence, assess every holding, and determine whether no, minor, "
    "or major rebalancing is supported without designing a target allocation."
)
ADAPTIVE_REBALANCE_PROMPT = (
    "Research a strategic target allocation using the validated diagnosis, investor constraints, and current "
    "source-backed evidence without calculating trades or quantities."
)


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "432",
        "email": "reviewer@example.com",
        "name": "Portfolio Reviewer",
        "avatar": "",
    }


def _market() -> portfolio_market_data.MarketDefinition:
    market = portfolio_market_data.resolve_market("US")
    assert market is not None
    return market


def _body(
    *,
    include_rebalance: bool = False,
    additional_budget: str = "",
    scenario: str = "",
) -> review_schemas.ReviewPortfolioRequest:
    return review_schemas.ReviewPortfolioRequest(
        holdings="AAPL, 10, 80",
        risk_tolerance="Moderate",
        investment_goals="Long-term capital growth with manageable concentration.",
        target_market="US",
        investment_horizon="Long-term (3-5 years)",
        scenario=scenario,
        include_rebalance=include_rebalance,
        available_cash="100",
        additional_budget=additional_budget,
        allow_fractional_shares=False,
        minimum_trade_amount="25",
        tax_context="taxable",
    )


def _quote(ticker: str, price: str = "100") -> portfolio_market_data.MarketQuote:
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=ticker,
        price=Decimal(price),
        currency="USD",
        exchange="NMS",
        retrieved_at=datetime.datetime.now(datetime.UTC),
        asset_type="stock",
        display_name=f"{ticker} Corporation",
        market_cap=Decimal("3000000000000"),
        average_volume=50_000_000,
        sector="Technology",
    )


def _review_data(
    *,
    need: str = "major",
    scenario: str = "",
    summary: str = "The portfolio has a quality holding but material single-stock concentration.",
) -> dict[str, object]:
    scenario_assessment = None
    if scenario:
        scenario_assessment = {
            "scenario": scenario,
            "portfolio_impact": "The scenario could pressure the holding's valuation and portfolio value.",
            "vulnerable_tickers": ["AAPL"],
            "resilient_tickers": [],
            "source_ids": ["S1"],
        }
    recommendation = "HOLD" if need == "none" else "TRIM"
    return {
        "as_of": TODAY.isoformat(),
        "market": "US",
        "portfolio_summary": summary,
        "diversification_findings": [
            {"statement": "A single holding creates material concentration risk.", "source_ids": ["S1"]}
        ],
        "position_assessments": [
            {
                "ticker": "AAPL",
                "fundamental_status": "healthy",
                "recommendation": recommendation,
                "assessment": "The company remains profitable, but portfolio concentration requires attention.",
                "portfolio_fit": "Suitable as a growth holding when sized consistently with the investor profile.",
                "source_ids": ["S1"],
            }
        ],
        "scenario_assessment": scenario_assessment,
        "portfolio_risks": [{"statement": "Company-specific risk dominates portfolio outcomes.", "source_ids": ["S1"]}],
        "urgent_actions": (
            [] if need == "none" else [{"statement": "Set a maximum acceptable position weight.", "source_ids": ["S1"]}]
        ),
        "review_triggers": [{"statement": "Review the holding after its next earnings release.", "source_ids": ["S1"]}],
        "tax_considerations": [{"statement": "Review tax lots before selling shares.", "source_ids": ["S1"]}],
        "rebalance_assessment": {
            "need": need,
            "confidence": "high",
            "urgency": "monitor" if need == "none" else "near_term",
            "summary": (
                "No meaningful allocation change is currently supported."
                if need == "none"
                else "Material concentration supports meaningful diversification."
            ),
            "drivers": [
                {
                    "statement": (
                        "Ordinary monitoring is proportionate to current evidence."
                        if need == "none"
                        else "The verified holding represents most current portfolio value."
                    ),
                    "source_ids": ["S1"],
                }
            ],
        },
        "assumptions": ["The investor accepts United States dollar exposure."],
        "sources": [
            {
                "id": "S1",
                "title": "Company quarterly results",
                "publisher": "AAPL",
                "published_at": (TODAY - datetime.timedelta(days=2)).isoformat(),
                "url": "https://example.com/aapl-results",
            }
        ],
    }


def _rebalance_data() -> dict[str, object]:
    return {
        "as_of": TODAY.isoformat(),
        "market": "US",
        "strategy_summary": "Retain evidence-backed growth exposure while reducing avoidable concentration.",
        "allocations": [
            {
                "ticker": "AAPL",
                "target_weight_pct": 50,
                "role": "Existing growth",
                "rationale": "Retains the validated quality exposure at a materially lower concentration.",
                "source_ids": ["R1"],
            },
            {
                "ticker": "VTI",
                "target_weight_pct": 50,
                "role": "Diversified core",
                "rationale": "Adds broad equity exposure to reduce company-specific portfolio risk.",
                "source_ids": ["R1"],
            },
        ],
        "portfolio_risks": [
            {"statement": "Equity-market risk remains material after diversification.", "source_ids": ["R1"]}
        ],
        "execution_guidance": [{"statement": "Verify live prices before placing any order.", "source_ids": ["R1"]}],
        "tax_considerations": [{"statement": "Review account tax treatment before selling.", "source_ids": ["R1"]}],
        "assumptions": ["The investor accepts broad United States equity exposure."],
        "sources": [
            {
                "id": "R1",
                "title": "Current company results",
                "publisher": "AAPL",
                "published_at": (TODAY - datetime.timedelta(days=1)).isoformat(),
                "url": "https://example.com/aapl-current",
            }
        ],
    }


def _task_settings() -> types.SimpleNamespace:
    review_prompt_client = object()
    review_client = object()
    rebalance_prompt_client = object()
    rebalance_client = object()
    prompt_task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="LowCost",
        model="gpt-5.6-luna",
        web_search=False,
        reasoning_level="low",
    )
    premium_task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="Premium",
        model="gpt-5.6-terra",
        web_search=True,
        reasoning_level="high",
    )
    clients = {
        "REVIEW_PORTFOLIO_BUILD_PROMPT": review_prompt_client,
        "REVIEW_PORTFOLIO_ANALYZE": review_client,
        "REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT": rebalance_prompt_client,
        "REVIEW_PORTFOLIO_REBALANCE_ANALYZE": rebalance_client,
    }
    return types.SimpleNamespace(
        get_ai_client=lambda task_id: clients.get(task_id),
        tasks={
            "REVIEW_PORTFOLIO_BUILD_PROMPT": prompt_task,
            "REVIEW_PORTFOLIO_ANALYZE": premium_task,
            "REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT": prompt_task,
            "REVIEW_PORTFOLIO_REBALANCE_ANALYZE": premium_task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


def _review_payload_data(
    body: review_schemas.ReviewPortfolioRequest | None = None,
    *,
    need: str = "major",
) -> dict[str, object]:
    request = body or _body(include_rebalance=True)
    market = _market()
    holdings = portfolio_rebalance.parse_holdings(request.holdings, market)
    settings = portfolio_rebalance.parse_settings(
        available_cash=request.available_cash,
        fractional_shares=request.allow_fractional_shares,
        minimum_trade_amount=request.minimum_trade_amount,
        tax_context=request.tax_context,
    )
    budget = build_portfolio.parse_budget(request.additional_budget, market)
    snapshot = portfolio_rebalance.build_snapshot(holdings, {"AAPL": _quote("AAPL")}, settings.available_cash)
    report = review_portfolio.parse_review_research(
        json.dumps(_review_data(need=need, scenario=request.scenario)),
        market,
        ("AAPL",),
        request.scenario,
    )
    return review_portfolio.build_review_payload(
        request,
        market,
        settings,
        budget,
        snapshot,
        report,
    ).model_dump(mode="json")


def _rebalance_payload_data(
    body: review_schemas.ReviewPortfolioRequest | None = None,
) -> dict[str, object]:
    request = body or _body(include_rebalance=True)
    market = _market()
    holdings = portfolio_rebalance.parse_holdings(request.holdings, market)
    settings = portfolio_rebalance.parse_settings(
        available_cash=request.available_cash,
        fractional_shares=request.allow_fractional_shares,
        minimum_trade_amount=request.minimum_trade_amount,
        tax_context=request.tax_context,
    )
    budget = build_portfolio.parse_budget(request.additional_budget, market)
    plan_settings = review_portfolio.planning_settings(settings, budget)
    quotes = {"AAPL": _quote("AAPL"), "VTI": _quote("VTI")}
    snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, plan_settings.available_cash)
    research = review_portfolio.parse_rebalance_research(json.dumps(_rebalance_data()), market)
    plan = portfolio_rebalance.calculate_plan(
        snapshot,
        review_portfolio.to_plan_recommendation(research),
        quotes,
        market,
        plan_settings,
    )
    plan = review_portfolio.apply_budget_warnings(plan, budget)
    return review_portfolio.build_rebalance_payload(
        market,
        settings,
        budget,
        research,
        plan,
    ).model_dump(mode="json")


def test_request_and_research_schemas_are_strict() -> None:
    request = _body(additional_budget="$1,000 monthly")
    assert request.additional_budget == "$1,000 monthly"

    for invalid in (
        {"holdings": "AAPL, 10", "target_market": "US", "risk_tolerance": "Extreme"},
        {"holdings": "AAPL, 10\u0000", "target_market": "US"},
        {"holdings": "AAPL, 10", "target_market": "US\nAU"},
        {"holdings": "AAPL, 10", "target_market": "US", "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            review_schemas.ReviewPortfolioRequest.model_validate(invalid)

    for schema in (review_portfolio.review_response_schema(), review_portfolio.rebalance_response_schema()):
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


def test_review_validation_requires_complete_recent_position_evidence() -> None:
    valid = review_portfolio.parse_review_research(
        json.dumps(_review_data()),
        _market(),
        ("AAPL",),
        "",
    )
    assert valid.position_assessments[0].ticker == "AAPL"

    missing = copy.deepcopy(_review_data())
    missing["position_assessments"] = []
    with pytest.raises(review_portfolio.ReviewResearchError):
        review_portfolio.parse_review_research(json.dumps(missing), _market(), ("AAPL",), "")

    stale = copy.deepcopy(_review_data())
    stale["sources"][0]["published_at"] = (TODAY - datetime.timedelta(days=181)).isoformat()  # type: ignore[index]
    with pytest.raises(review_portfolio.ReviewResearchError):
        review_portfolio.parse_review_research(json.dumps(stale), _market(), ("AAPL",), "")


def test_target_allocation_must_follow_validated_position_actions() -> None:
    body = _body()
    market = _market()
    holdings = portfolio_rebalance.parse_holdings(body.holdings, market)
    snapshot = portfolio_rebalance.build_snapshot(holdings, {"AAPL": _quote("AAPL")}, Decimal("100"))
    review = review_portfolio.parse_review_research(
        json.dumps(_review_data(need="major")),
        market,
        ("AAPL",),
        "",
    )
    aligned = review_portfolio.parse_rebalance_research(json.dumps(_rebalance_data()), market)
    review_portfolio.validate_rebalance_alignment(aligned, review, snapshot)

    inconsistent_data = copy.deepcopy(_rebalance_data())
    inconsistent_data["allocations"] = [
        {
            "ticker": "AAPL",
            "target_weight_pct": 100,
            "role": "Concentrated core",
            "rationale": "This contradicts the validated recommendation to reduce the current position.",
            "source_ids": ["R1"],
        }
    ]
    inconsistent = review_portfolio.parse_rebalance_research(json.dumps(inconsistent_data), market)
    with pytest.raises(review_portfolio.RebalanceResearchError, match="does not reduce AAPL"):
        review_portfolio.validate_rebalance_alignment(inconsistent, review, snapshot)

    hold_review = review_portfolio.parse_review_research(
        json.dumps(_review_data(need="none")),
        market,
        ("AAPL",),
        "",
    )
    with pytest.raises(review_portfolio.RebalanceResearchError, match="validated HOLD"):
        review_portfolio.validate_rebalance_alignment(aligned, hold_review, snapshot)

    hold_plan = portfolio_rebalance.calculate_plan(
        snapshot,
        review_portfolio.to_plan_recommendation(aligned),
        {"AAPL": _quote("AAPL"), "VTI": _quote("VTI")},
        market,
        portfolio_rebalance.parse_settings(
            available_cash="100",
            fractional_shares=False,
            minimum_trade_amount="0",
            tax_context="unknown",
        ),
    )
    with pytest.raises(portfolio_rebalance.RebalanceCalculationError, match="validated HOLD"):
        review_portfolio.validate_plan_alignment(hold_plan, hold_review)


@pytest.mark.asyncio
async def test_review_only_verifies_holdings_uses_two_models_and_caches_structured_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe_summary = "<script>alert('review')</script>"
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_REVIEW_PROMPT),
            ai.AIResponse(completion=json.dumps(_review_data(summary=unsafe_summary))),
        ]
    )
    fetch_quotes = mock.AsyncMock(side_effect=lambda symbols, _market: {ticker: _quote(ticker) for ticker in symbols})
    set_cache = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_router.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(review_router.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(review_router.analysis_cache, "set_cached_payload", set_cache)

    response = await review_router.review_portfolio_stream(_body(), user=_user())
    events = await _collect_stream_events(response)

    assert len(execute.await_args_list) == 2
    assert events[-1] == {"type": "complete", "status": "review_only"}
    review_event = next(event for event in events if event["type"] == "review_result")
    assert "&lt;script&gt;alert" in review_event["html"]
    assert "<script>alert" not in review_event["html"]
    assert "Rebalance assessment" in review_event["html"]
    fetch_quotes.assert_awaited_once_with(("AAPL",), _market())
    assert '"current_price": "100"' in execute.await_args_list[0].args[2]
    assert execute.await_args_list[1].kwargs["schema_name"] == "portfolio_review_research"
    set_cache.assert_awaited_once()
    cache_call = set_cache.await_args
    assert cache_call.kwargs["feature"] == "review-portfolio"
    assert cache_call.kwargs["ttl_seconds"] == 72 * 60 * 60
    assert cache_call.kwargs["inputs"]["additional_budget"] == ""
    assert cache_call.kwargs["payload"]["positions"][0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_no_rebalance_need_skips_both_allocation_models(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_REVIEW_PROMPT),
            ai.AIResponse(completion=json.dumps(_review_data(need="none"))),
        ]
    )
    set_cache = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_router.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(
        review_router.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(return_value={"AAPL": _quote("AAPL")}),
    )
    monkeypatch.setattr(review_router.analysis_cache, "set_cached_payload", set_cache)

    response = await review_router.review_portfolio_stream(
        _body(include_rebalance=True),
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert len(execute.await_args_list) == 2
    assert any(event["type"] == "rebalance_skipped" for event in events)
    assert events[-1] == {"type": "complete", "status": "not_needed"}
    set_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_rebalance_uses_four_focused_models_and_next_monthly_contribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_REVIEW_PROMPT),
            ai.AIResponse(completion=json.dumps(_review_data(need="major"))),
            ai.AIResponse(completion=ADAPTIVE_REBALANCE_PROMPT),
            ai.AIResponse(completion=json.dumps(_rebalance_data())),
        ]
    )
    fetch_quotes = mock.AsyncMock(side_effect=lambda symbols, _market: {ticker: _quote(ticker) for ticker in symbols})
    set_cache = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_router.config, "ai_task_settings", settings)
    monkeypatch.setattr(review_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(review_router.portfolio_market_data, "fetch_quotes", fetch_quotes)
    monkeypatch.setattr(review_router.analysis_cache, "set_cached_payload", set_cache)

    response = await review_router.review_portfolio_stream(
        _body(include_rebalance=True, additional_budget="$1,000 monthly"),
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert len(execute.await_args_list) == 4
    assert events[-1] == {"type": "complete", "status": "success"}
    rebalance_event = next(event for event in events if event["type"] == "rebalance_result")
    assert rebalance_event["plan"]["cash_before"] == 1100
    trades = {trade["ticker"]: trade for trade in rebalance_event["plan"]["trades"]}
    assert trades["VTI"]["action"] == "BUY"
    assert trades["VTI"]["trade_quantity"] == 10
    assert "next monthly contribution only" in rebalance_event["html"]
    assert "Source-backed target allocation" in rebalance_event["html"]
    assert execute.await_args_list[1].kwargs["schema_name"] == "portfolio_review_research"
    assert execute.await_args_list[3].kwargs["schema_name"] == "portfolio_rebalance_research"
    assert execute.await_args_list[1].args[1].web_search is True
    assert execute.await_args_list[3].args[1].web_search is True
    assert set_cache.await_count == 2
    review_cache, plan_cache = set_cache.await_args_list
    assert review_cache.kwargs["ttl_seconds"] == 72 * 60 * 60
    assert plan_cache.kwargs["feature"] == "review-portfolio-rebalance"
    assert plan_cache.kwargs["ttl_seconds"] == 15 * 60
    assert plan_cache.kwargs["payload"]["additional_budget"]["cadence"] == "monthly"


@pytest.mark.asyncio
async def test_review_and_rebalance_each_allow_one_conditional_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_REVIEW_PROMPT),
            ai.AIResponse(completion="invalid review"),
            ai.AIResponse(completion=json.dumps(_review_data(need="major"))),
            ai.AIResponse(completion=ADAPTIVE_REBALANCE_PROMPT),
            ai.AIResponse(completion="invalid allocation"),
            ai.AIResponse(completion=json.dumps(_rebalance_data())),
        ]
    )
    monkeypatch.setattr(review_router.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(
        review_router.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(side_effect=lambda symbols, _market: {ticker: _quote(ticker) for ticker in symbols}),
    )
    monkeypatch.setattr(
        review_router.analysis_cache,
        "set_cached_payload",
        mock.AsyncMock(return_value=True),
    )

    response = await review_router.review_portfolio_stream(
        _body(include_rebalance=True),
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert len(execute.await_args_list) == 6
    assert "failed application validation" in execute.await_args_list[2].args[2]
    assert "failed application validation" in execute.await_args_list[5].args[2]
    assert events[-1] == {"type": "complete", "status": "success"}


@pytest.mark.asyncio
async def test_rebalance_failure_preserves_cached_and_streamed_review(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_REVIEW_PROMPT),
            ai.AIResponse(completion=json.dumps(_review_data(need="major"))),
            ai.AIResponse(success=False, error="provider details"),
        ]
    )
    set_cache = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(review_router.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(review_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(
        review_router.portfolio_market_data,
        "fetch_quotes",
        mock.AsyncMock(return_value={"AAPL": _quote("AAPL")}),
    )
    monkeypatch.setattr(review_router.analysis_cache, "set_cached_payload", set_cache)

    response = await review_router.review_portfolio_stream(
        _body(include_rebalance=True),
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert any(event["type"] == "review_result" for event in events)
    rebalance_error = next(event for event in events if event["type"] == "rebalance_error")
    assert "provider details" not in rebalance_error["message"]
    assert events[-1] == {"type": "complete", "status": "rebalance_failed"}
    set_cache.assert_awaited_once()
    assert set_cache.await_args.kwargs["feature"] == "review-portfolio"


@pytest.mark.asyncio
async def test_invalid_holdings_stop_before_market_data_and_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = mock.AsyncMock()
    fetch_quotes = mock.AsyncMock()
    monkeypatch.setattr(review_router.ai, "execute_task_prompt", execute)
    monkeypatch.setattr(review_router.portfolio_market_data, "fetch_quotes", fetch_quotes)

    response = await review_router.review_portfolio_stream(
        _body().model_copy(update={"holdings": "AAPL 10 shares"}),
        user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "error"
    assert "Line 1" in events[-1]["message"]
    execute.assert_not_awaited()
    fetch_quotes.assert_not_awaited()


def test_page_renders_structured_caches_and_post_storage_ui(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _body(include_rebalance=True)
    market = _market()
    settings = portfolio_rebalance.parse_settings(
        available_cash=body.available_cash,
        fractional_shares=body.allow_fractional_shares,
        minimum_trade_amount=body.minimum_trade_amount,
        tax_context=body.tax_context,
    )
    inputs = review_portfolio.cache_inputs(body, market, settings)
    generated_at = datetime.datetime.now(datetime.UTC).isoformat()
    cached_review = {
        **inputs,
        "payload": _review_payload_data(body),
        "generated_at": generated_at,
    }
    cached_rebalance = {
        **inputs,
        "payload": _rebalance_payload_data(body),
        "generated_at": generated_at,
    }
    get_cache = mock.AsyncMock(side_effect=[cached_review, cached_rebalance])
    monkeypatch.setattr(review_router.analysis_cache, "get_cached_payload", get_cache)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/review-portfolio")

    assert response.status_code == 200
    assert get_cache.await_count == 2
    assert "The portfolio has a quality holding" in response.text
    assert "Source-backed target allocation" in response.text
    assert "REVIEW_STORAGE_VERSION = 3" in response.text
    assert "additional_budget" in response.text
    assert "fetch('/review-portfolio/stream'" in response.text
    assert "method: 'POST'" in response.text
    assert "marked/marked.min.js" not in response.text
    assert "sanitizeMarkdownFragment" not in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    assert "draft-portfolio-intent.js" in response.text
    assert 'data-portfolio-intent-handoff-target="review"' in response.text
    assert "safeCsvCell" in response.text
    assert "portfolio-rebalance-" in response.text

    handoff_script = pathlib.Path("app/static/js/draft-portfolio-intent.js").read_text(encoding="utf-8")
    assert "handoff.target !== expectedTarget" in handoff_script
    assert "window.confirm('Replace the existing portfolio intent" in handoff_script


def test_stream_is_post_only_and_requires_strict_body(client: TestClient) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    assert client.get("/review-portfolio/stream", params={"holdings": "AAPL, 10"}).status_code == 405
    response = client.post(
        "/review-portfolio/stream",
        json={"holdings": "AAPL, 10"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 422


def test_cache_payloads_enforce_separate_freshness_windows() -> None:
    review_payload = _review_payload_data()
    rebalance_payload = _rebalance_payload_data()
    assert review_portfolio.is_valid_review_cache_payload(review_payload) is True
    assert review_portfolio.is_valid_rebalance_cache_payload(rebalance_payload) is True

    stale_review = copy.deepcopy(review_payload)
    stale_review["generated_at"] = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=74)).isoformat()
    stale_plan = copy.deepcopy(rebalance_payload)
    stale_plan["generated_at"] = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=17)).isoformat()
    assert review_portfolio.is_valid_review_cache_payload(stale_review) is False
    assert review_portfolio.is_valid_rebalance_cache_payload(stale_plan) is False


def test_page_returns_service_error_without_supported_market(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review_router.config.app_settings, "primary_markets", {"LSE"})
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/review-portfolio")

    assert response.status_code == 503
    assert response.text == "At least one supported primary market (US, AU, or VN) is required."
