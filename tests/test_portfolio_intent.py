import json
import types
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import config as app_config
from app.routers import portfolio_intent
from app.schemas import portfolio_intent as portfolio_intent_schemas
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "intent-user",
        "email": "intent@example.com",
        "name": "Intent User",
        "avatar": "",
    }


def _task_settings(*, client: object | None = None) -> types.SimpleNamespace:
    resolved_client = object() if client is None else client
    task = types.SimpleNamespace(
        model="gpt-5.6-luna",
        web_search=False,
        reasoning_level="low",
    )
    return types.SimpleNamespace(
        get_ai_client=lambda task_id: resolved_client if task_id == "DRAFT_PORTFOLIO_INTENT" else None,
        tasks={"DRAFT_PORTFOLIO_INTENT": task},
    )


def test_standalone_page_exposes_editable_result_and_handoffs(client: TestClient) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/draft-portfolio-intent")

    assert response.status_code == 200
    assert 'id="draft-portfolio-intent-form"' in response.text
    assert 'id="draft-portfolio-intent-result"' in response.text
    assert 'data-portfolio-intent-destination="build"' in response.text
    assert 'data-portfolio-intent-destination="review"' in response.text
    assert "draft-portfolio-intent.js" in response.text
    assert "Draft Portfolio Intent" in response.text


def test_standalone_page_requires_authentication(client: TestClient) -> None:
    response = client.get("/draft-portfolio-intent", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_drafts_complete_intent_with_strict_output(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = {
        "status": "complete",
        "intent": (
            "Long-term income | Australia | AUD 100,000 | Moderate risk | Multi-year | ETFs and stocks.\n\n"
            "Focus on sustainable yield, payout health, franking credits, and imminent distribution risks."
        ),
        "questions": [],
    }
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    settings = _task_settings()
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", settings)
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    body = portfolio_intent_schemas.DraftIntentRequest(
        market_country="ASX / Australia",
        portfolio_type="Long-term income",
        budget="AUD 100,000",
        risk_tolerance="Moderate",
        holding_horizon="Multi-year",
        instrument_preference="ETFs and stocks",
        payout_frequency_preference="Prefer quarterly",
        market_specific_mechanics="Account for franking credits",
    )

    result = await portfolio_intent.draft_portfolio_intent(body, _user())

    assert result.status == "complete"
    assert result.intent == completion["intent"]
    assert result.questions == []
    call = execute.await_args
    assert call.args[0] is not None
    assert call.args[1] is settings.tasks["DRAFT_PORTFOLIO_INTENT"]
    prompt = call.args[2]
    assert '"market_country": "ASX / Australia"' in prompt
    assert '"payout_frequency_preference": "Prefer quarterly"' in prompt
    assert '"allocation_split"' not in prompt
    assert "Do not build or review a portfolio" in prompt
    assert call.kwargs == {
        "response_json_schema": portfolio_intent_schemas.DraftIntentResponse.model_json_schema(),
        "schema_name": "portfolio_intent_draft",
    }


@pytest.mark.asyncio
async def test_returns_targeted_clarification_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = {
        "status": "needs_clarification",
        "intent": None,
        "questions": [
            {
                "id": "portfolio_objective",
                "question": "Is the primary objective growth, income, swing trading, or a blend?",
            },
            {
                "id": "target_market",
                "question": "Which market or country should the portfolio use?",
            },
        ],
    }
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)

    result = await portfolio_intent.draft_portfolio_intent(
        portfolio_intent_schemas.DraftIntentRequest(),
        _user(),
    )

    assert result.status == "needs_clarification"
    assert result.intent is None
    assert [question.id for question in result.questions] == ["portfolio_objective", "target_market"]


@pytest.mark.asyncio
async def test_clarification_answers_are_forwarded_as_data(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = {
        "status": "complete",
        "intent": "Balanced | US | Moderate risk | Multi-year.\n\nFocus on quality growth and durable income.",
        "questions": [],
    }
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    body = portfolio_intent_schemas.DraftIntentRequest(
        clarifications={
            "portfolio_objective": "Balanced growth and income",
            "target_market": "US",
        }
    )

    await portfolio_intent.draft_portfolio_intent(body, _user())

    prompt = execute.await_args.args[2]
    assert '"portfolio_objective": "Balanced growth and income"' in prompt
    assert '"target_market": "US"' in prompt


@pytest.mark.asyncio
async def test_missing_task_configuration_returns_service_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = types.SimpleNamespace(get_ai_client=lambda _task_id: None, tasks={})
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(
            portfolio_intent_schemas.DraftIntentRequest(),
            _user(),
        )

    assert exc_info.value.status_code == 503
    assert "DRAFT_PORTFOLIO_INTENT" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ai_response",
    [
        ai.AIResponse(success=False, error="provider unavailable"),
        ai.AIResponse(completion='{"status":"complete","intent":null,"questions":[]}'),
    ],
)
async def test_provider_and_invalid_output_fail_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    ai_response: ai.AIResponse,
) -> None:
    execute = mock.AsyncMock(return_value=ai_response)
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)

    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(
            portfolio_intent_schemas.DraftIntentRequest(portfolio_type="Long-term growth"),
            _user(),
        )

    assert exc_info.value.status_code == 502


def test_endpoint_accepts_sparse_request_and_rejects_unknown_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = {
        "status": "needs_clarification",
        "intent": None,
        "questions": [{"id": "portfolio_objective", "question": "What is the primary objective?"}],
    }
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.post("/portfolio-intent/draft", json={})
    invalid_response = client.post("/portfolio-intent/draft", json={"unsupported": "value"})

    assert response.status_code == 200
    assert response.json() == completion
    assert invalid_response.status_code == 422


def test_task_policy_uses_luna_low_without_web_search() -> None:
    task = app_config.ai_task_settings.tasks["DRAFT_PORTFOLIO_INTENT"]

    assert task.vendor == "AzureOpenAI"
    assert task.tier == "LowCost"
    assert task.model == "gpt-5.6-luna"
    assert task.reasoning_level == "low"
    assert task.web_search is False
