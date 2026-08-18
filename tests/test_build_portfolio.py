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
ADAPTIVE_ACTION_PROMPT = (
    "Prioritize every deterministic portfolio action, explain its implementation sequence, and use only the supplied "
    "action IDs and source references without changing securities, target weights, sizes, or calculated quantities."
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
    transition_mode: str = "contribution_only",
) -> build_portfolio_schemas.BuildPortfolioRequest:
    return build_portfolio_schemas.BuildPortfolioRequest(
        risk_tolerance="Moderate",
        portfolio_intent="Build a diversified long-term ETF portfolio balancing growth and income.",
        target_market="US",
        investment_horizon="Long-term (3-5 years)",
        budget=budget,
        allow_fractional_shares=allow_fractional_shares,
        existing_holdings=existing_holdings,
        transition_mode=transition_mode,
    )


def _task_settings() -> types.SimpleNamespace:
    prompt_client = object()
    research_client = object()
    action_prompt_client = object()
    action_client = object()
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
    action_prompt_task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="LowCost",
        model="gpt-5.6-luna",
        web_search=False,
        reasoning_level="low",
    )
    action_task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="Premium",
        model="gpt-5.6-terra",
        web_search=False,
        reasoning_level="high",
    )
    clients = {
        "BUILD_PORTFOLIO_BUILD_PROMPT": prompt_client,
        "BUILD_PORTFOLIO_ANALYZE": research_client,
        "BUILD_PORTFOLIO_ACTION_BUILD_PROMPT": action_prompt_client,
        "BUILD_PORTFOLIO_ACTION_PLAN": action_client,
    }
    return types.SimpleNamespace(
        prompt_client=prompt_client,
        research_client=research_client,
        action_prompt_client=action_prompt_client,
        action_client=action_client,
        prompt_task=prompt_task,
        research_task=research_task,
        action_prompt_task=action_prompt_task,
        action_task=action_task,
        get_ai_client=lambda task_id: clients.get(task_id),
        tasks={
            "BUILD_PORTFOLIO_BUILD_PROMPT": prompt_task,
            "BUILD_PORTFOLIO_ANALYZE": research_task,
            "BUILD_PORTFOLIO_ACTION_BUILD_PROMPT": action_prompt_task,
            "BUILD_PORTFOLIO_ACTION_PLAN": action_task,
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


def _action_plan_data(action_ids: tuple[str, ...] = ("A1", "A2", "A3")) -> dict[str, object]:
    priorities = ("High", "Medium", "Low")
    return {
        "summary": "Establish the diversified core first, then complete defensive exposure and retain liquidity.",
        "actions": [
            {
                "action_id": action_id,
                "priority": priorities[min(index, len(priorities) - 1)],
                "rationale": f"Action {action_id} advances the validated portfolio in a controlled sequence.",
                "dependency_ids": ["A1"] if index == 1 else [],
                "source_ids": ["S1"] if index == 0 else ["S2"] if index < 3 else [],
            }
            for index, action_id in enumerate(action_ids)
        ],
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


def _action_payload_data() -> dict[str, object]:
    request = _request()
    market = _market()
    budget = build_portfolio.parse_budget(request.budget, market)
    report = build_portfolio.parse_research(json.dumps(_research_data()), market)
    payload = build_portfolio.build_payload(
        request,
        market,
        budget,
        report,
        _recommendation_quotes(),
        [],
        {},
    )
    candidates, warnings = build_portfolio.build_action_candidates(
        request,
        market,
        budget,
        report,
        payload,
        (),
        _recommendation_quotes(),
    )
    research = build_portfolio.parse_action_plan_research(
        json.dumps(_action_plan_data()),
        candidates,
        report,
    )
    return build_portfolio.build_action_plan_payload(
        request,
        market,
        payload,
        candidates,
        research,
        warnings,
    ).model_dump(mode="json")


def test_request_schema_and_budget_modes() -> None:
    request = _request(budget="$1,000 monthly")
    assert request.allow_fractional_shares is False
    assert request.transition_mode == "contribution_only"

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
            "transition_mode": "sell_everything",
        },
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
    for schema in (build_portfolio.response_schema(), build_portfolio.action_plan_response_schema()):
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


def test_action_candidates_preserve_contribution_only_and_allow_opt_in_exit() -> None:
    market = _market()
    report = build_portfolio.parse_research(json.dumps(_research_data()), market)
    holding = build_portfolio.parse_existing_holdings("AAPL, 2, 150", market)
    holding_quote = _quote("AAPL", "200", asset_type="stock", sector="Technology")
    verified_holdings = build_portfolio.build_verified_holdings(holding, {"AAPL": holding_quote})

    contribution_request = _request(existing_holdings="AAPL, 2, 150")
    budget = build_portfolio.parse_budget(contribution_request.budget, market)
    payload = build_portfolio.build_payload(
        contribution_request,
        market,
        budget,
        report,
        _recommendation_quotes(),
        verified_holdings,
        {"AAPL": holding_quote},
    )
    contribution_candidates, _ = build_portfolio.build_action_candidates(
        contribution_request,
        market,
        budget,
        report,
        payload,
        holding,
        {**_recommendation_quotes(), "AAPL": holding_quote},
    )
    assert {candidate.ticker: candidate.action for candidate in contribution_candidates}["AAPL"] == "HOLD"

    transition_request = contribution_request.model_copy(update={"transition_mode": "allow_trades"})
    transition_candidates, warnings = build_portfolio.build_action_candidates(
        transition_request,
        market,
        budget,
        report,
        payload,
        holding,
        {**_recommendation_quotes(), "AAPL": holding_quote},
    )
    transition_actions = {candidate.ticker: candidate for candidate in transition_candidates}
    assert transition_actions["AAPL"].action == "EXIT"
    assert transition_actions["AAPL"].sizing_pct == 100
    assert transition_actions["AAPL"].estimated_quantity == 2
    assert any("tax lots" in warning for warning in warnings)


def test_action_plan_requires_complete_valid_priority_dependencies() -> None:
    request = _request()
    market = _market()
    budget = build_portfolio.parse_budget(request.budget, market)
    report = build_portfolio.parse_research(json.dumps(_research_data()), market)
    payload = build_portfolio.build_payload(
        request,
        market,
        budget,
        report,
        _recommendation_quotes(),
        [],
        {},
    )
    candidates, warnings = build_portfolio.build_action_candidates(
        request,
        market,
        budget,
        report,
        payload,
        (),
        _recommendation_quotes(),
    )
    research = build_portfolio.parse_action_plan_research(
        json.dumps(_action_plan_data()),
        candidates,
        report,
    )
    action_payload = build_portfolio.build_action_plan_payload(
        request,
        market,
        payload,
        candidates,
        research,
        warnings,
    )

    assert [action.ticker for action in action_payload.actions] == ["VTI", "BND", "CASH"]
    assert [action.priority for action in action_payload.actions] == ["High", "Medium", "Low"]
    assert action_payload.actions[1].dependencies == ["BUY VTI"]

    incomplete = _action_plan_data(("A1", "A2"))
    with pytest.raises(build_portfolio.ActionPlanError, match="every deterministic action"):
        build_portfolio.parse_action_plan_research(json.dumps(incomplete), candidates, report)

    invalid_dependency = _action_plan_data()
    invalid_dependency["actions"][0]["dependency_ids"] = ["A3"]  # type: ignore[index]
    with pytest.raises(build_portfolio.ActionPlanError, match="higher-priority"):
        build_portfolio.parse_action_plan_research(json.dumps(invalid_dependency), candidates, report)


@pytest.mark.asyncio
async def test_successful_stream_uses_four_focused_models_and_separate_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _task_settings()
    unsafe_summary = '<script>alert("portfolio")</script>'
    unsafe_action = _action_plan_data()
    unsafe_action["actions"][0]["rationale"] = '<script>alert("action")</script>'  # type: ignore[index]
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data(strategy_summary=unsafe_summary))),
            ai.AIResponse(completion=ADAPTIVE_ACTION_PROMPT),
            ai.AIResponse(completion=json.dumps(unsafe_action)),
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

    assert execute.await_count == 4
    prompt_call, research_call, action_prompt_call, action_call = execute.await_args_list
    assert prompt_call.args[:2] == (settings.prompt_client, settings.prompt_task)
    assert "Act only as a prompt writer" in prompt_call.args[2]
    assert prompt_call.kwargs == {}
    assert research_call.args[:2] == (settings.research_client, settings.research_task)
    assert "Trusted server-owned role" in research_call.args[2]
    assert research_call.kwargs == {
        "response_json_schema": build_portfolio.response_schema(),
        "schema_name": "build_portfolio_research",
    }
    assert action_prompt_call.args[:2] == (settings.action_prompt_client, settings.action_prompt_task)
    assert "Act only as a prompt writer" in action_prompt_call.args[2]
    assert action_call.args[:2] == (settings.action_client, settings.action_task)
    assert "premium portfolio implementation planner" in action_call.args[2]
    assert action_call.kwargs == {
        "response_json_schema": build_portfolio.action_plan_response_schema(),
        "schema_name": "build_portfolio_action_plan",
    }
    fetch_quotes.assert_awaited_once_with(("VTI", "BND"), _market())
    assert set_cached_payload.await_count == 2
    allocation_cache, action_cache = set_cached_payload.await_args_list
    assert allocation_cache.kwargs["feature"] == "build-portfolio"
    assert allocation_cache.kwargs["inputs"]["portfolio_intent"] == request.portfolio_intent
    assert allocation_cache.kwargs["inputs"]["allow_fractional_shares"] == "false"
    assert allocation_cache.kwargs["inputs"]["transition_mode"] == "contribution_only"
    assert allocation_cache.kwargs["ttl_seconds"] == 72 * 60 * 60
    assert action_cache.kwargs["feature"] == "build-portfolio-action-plan"
    assert action_cache.kwargs["ttl_seconds"] == 15 * 60
    assert events[-1]["type"] == "result"
    assert unsafe_summary not in events[-1]["html"]
    assert "&lt;script&gt;alert" in events[-1]["html"]
    assert '<script>alert("action")</script>' not in events[-1]["html"]
    assert "&lt;script&gt;alert" in events[-1]["html"]
    assert "Prioritized action plan" in events[-1]["html"]


@pytest.mark.asyncio
async def test_invalid_research_gets_one_conditional_terra_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data(market="AU"))),
            ai.AIResponse(completion=json.dumps(_research_data())),
            ai.AIResponse(completion=ADAPTIVE_ACTION_PROMPT),
            ai.AIResponse(completion=json.dumps(_action_plan_data())),
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

    assert execute.await_count == 5
    correction_call = execute.await_args_list[2]
    assert correction_call.args[:2] == (settings.research_client, settings.research_task)
    assert "failed application validation" in correction_call.args[2]
    assert "does not match the selected market" in correction_call.args[2]
    assert correction_call.kwargs["response_json_schema"] == build_portfolio.response_schema()
    fetch_quotes.assert_awaited_once()
    assert set_cached_payload.await_count == 2
    assert events[-1]["type"] == "result"


@pytest.mark.asyncio
async def test_invalid_action_plan_gets_one_focused_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=ADAPTIVE_PROMPT),
            ai.AIResponse(completion=json.dumps(_research_data())),
            ai.AIResponse(completion=ADAPTIVE_ACTION_PROMPT),
            ai.AIResponse(completion=json.dumps(_action_plan_data(("A1", "A2")))),
            ai.AIResponse(completion=json.dumps(_action_plan_data())),
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

    assert execute.await_count == 5
    correction_call = execute.await_args_list[4]
    assert correction_call.args[:2] == (settings.action_client, settings.action_task)
    assert "portfolio action-plan response failed application validation" in correction_call.args[2]
    assert "annotate every deterministic action exactly once" in correction_call.args[2]
    assert correction_call.kwargs["response_json_schema"] == build_portfolio.action_plan_response_schema()
    assert set_cached_payload.await_count == 2
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
            ai.AIResponse(completion=ADAPTIVE_ACTION_PROMPT),
            ai.AIResponse(completion=json.dumps(_action_plan_data(("A1", "A2", "A3", "A4")))),
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
        "transition_mode": "contribution_only",
        "payload": _payload_data(strategy_summary=unsafe_summary),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    cached_action_plan = {
        "risk_tolerance": "Moderate",
        "portfolio_intent": "Balanced ETF portfolio",
        "target_market": "US",
        "investment_horizon": "Long-term (3-5 years)",
        "budget": "$10,000",
        "allow_fractional_shares": "false",
        "existing_holdings": "",
        "transition_mode": "contribution_only",
        "payload": _action_payload_data(),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    get_cached_payload = mock.AsyncMock(side_effect=[cached_result, cached_action_plan])
    monkeypatch.setattr(build_portfolio_router.analysis_cache, "get_cached_payload", get_cached_payload)
    monkeypatch.setattr(build_portfolio_router.config.app_settings, "primary_markets", {"US", "AU", "VN"})
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/build-portfolio")

    assert response.status_code == 200
    assert get_cached_payload.await_count == 2
    allocation_cache_call, action_cache_call = get_cached_payload.await_args_list
    assert allocation_cache_call.kwargs["feature"] == "build-portfolio"
    assert allocation_cache_call.kwargs["input_fields"] == (
        "risk_tolerance",
        "portfolio_intent",
        "target_market",
        "investment_horizon",
        "budget",
        "allow_fractional_shares",
        "existing_holdings",
        "transition_mode",
    )
    assert allocation_cache_call.kwargs["payload_validator"] is build_portfolio.is_valid_cache_payload
    assert action_cache_call.kwargs["feature"] == "build-portfolio-action-plan"
    assert action_cache_call.kwargs["payload_validator"] is build_portfolio.is_valid_action_plan_cache_payload
    assert unsafe_summary not in response.text
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in response.text
    assert 'name="portfolio_intent"' in response.text
    assert 'name="allow_fractional_shares"' in response.text
    assert "$10,000 or $1,000 monthly" in response.text
    assert "BUILD_PORTFOLIO_STORAGE_SCHEMA_VERSION = 3" in response.text
    assert 'name="transition_mode"' in response.text
    assert "Prioritized action plan" in response.text
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
            ai.AIResponse(completion=ADAPTIVE_ACTION_PROMPT),
            ai.AIResponse(completion=json.dumps(_action_plan_data())),
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
    assert execute.await_count == 4


def test_cache_and_task_policy() -> None:
    payload = _payload_data()
    assert build_portfolio.is_valid_cache_payload(payload) is True
    action_payload = _action_payload_data()
    assert build_portfolio.is_valid_action_plan_cache_payload(action_payload) is True

    stale = copy.deepcopy(payload)
    stale["generated_at"] = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=74)).isoformat()
    assert build_portfolio.is_valid_cache_payload(stale) is False
    stale_action = copy.deepcopy(action_payload)
    stale_action["generated_at"] = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=17)
    ).isoformat()
    assert build_portfolio.is_valid_action_plan_cache_payload(stale_action) is False

    prompt_task = config.ai_task_settings.tasks["BUILD_PORTFOLIO_BUILD_PROMPT"]
    research_task = config.ai_task_settings.tasks["BUILD_PORTFOLIO_ANALYZE"]
    action_prompt_task = config.ai_task_settings.tasks["BUILD_PORTFOLIO_ACTION_BUILD_PROMPT"]
    action_task = config.ai_task_settings.tasks["BUILD_PORTFOLIO_ACTION_PLAN"]
    assert prompt_task.model == "gpt-5.6-luna"
    assert prompt_task.web_search is False
    assert prompt_task.reasoning_level == "low"
    assert research_task.model == "gpt-5.6-terra"
    assert research_task.web_search is True
    assert research_task.reasoning_level == "medium"
    assert action_prompt_task.model == "gpt-5.6-luna"
    assert action_prompt_task.web_search is False
    assert action_prompt_task.reasoning_level == "low"
    assert action_task.model == "gpt-5.6-terra"
    assert action_task.web_search is False
    assert action_task.reasoning_level == "high"
