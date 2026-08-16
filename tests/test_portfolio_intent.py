import asyncio
import json
import pathlib
import types
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from app import config as app_config
from app.routers import portfolio_intent
from app.schemas import portfolio_intent as portfolio_intent_schemas
from app.services import auth
from app.services import portfolio_intent as portfolio_intent_service
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "intent-user",
        "email": "intent@example.com",
        "name": "Intent User",
        "avatar": "",
    }


def _request(*, disconnected: bool = False) -> Request:
    async def receive() -> dict[str, object]:
        if disconnected:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/portfolio-intent/draft", "headers": []},
        receive,
    )


def _task_settings(*, client: object | None = None) -> types.SimpleNamespace:
    resolved_client = object() if client is None else client
    task = types.SimpleNamespace(
        model="gpt-5.6-luna",
        web_search=False,
        reasoning_level="low",
    )
    return types.SimpleNamespace(
        client=resolved_client,
        task=task,
        get_ai_client=lambda task_id: resolved_client if task_id == "DRAFT_PORTFOLIO_INTENT" else None,
        tasks={"DRAFT_PORTFOLIO_INTENT": task},
    )


def _complete_response(
    intent: str = (
        "Long-term income | Australia | Moderate risk | Multi-year | ETFs and stocks.\n\n"
        "Emphasize sustainable yield, payout health, valuation discipline, and material concentration risks."
    ),
) -> dict[str, object]:
    return {
        "status": "complete",
        "intent": intent,
        "questions": [],
        "assumptions": ["Unspecified allocation weights remain flexible."],
    }


def _clarification_response() -> dict[str, object]:
    return {
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
        "assumptions": [],
    }


def test_standalone_page_exposes_persistent_draft_and_structured_handoffs(client: TestClient) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/draft-portfolio-intent")

    assert response.status_code == 200
    assert 'id="draft-portfolio-intent-form"' in response.text
    assert 'name="destination"' not in response.text
    assert "Draft for" not in response.text
    assert 'id="draft-portfolio-intent-result"' in response.text
    assert 'data-portfolio-intent-destination="build"' in response.text
    assert 'data-portfolio-intent-destination="review"' in response.text
    assert "draft-portfolio-intent.js" in response.text

    script = pathlib.Path("app/static/js/draft-portfolio-intent.js").read_text(encoding="utf-8")
    assert "AlphaLabStorage.load(STORAGE_FEATURE" in script
    assert "AlphaLabStorage.save(STORAGE_FEATURE" in script
    assert "status: draftStatus" in script
    assert "intent: draftStatus === 'complete'" in script
    assert "assumptions: draftStatus === 'complete'" in script
    assert "HANDOFF_MAX_AGE_MS = 30 * 60 * 1000" in script
    assert "STORAGE_SCHEMA_VERSION = 2" in script
    assert "fields: buildHandoffFields()" in script
    assert "clearGeneratedState()" in script
    assert "removeItem(LEGACY_HANDOFF_KEY)" in script


def test_standalone_page_requires_authentication(client: TestClient) -> None:
    response = client.get("/draft-portfolio-intent", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_request_schema_enforces_enums_controls_and_rounds() -> None:
    request = portfolio_intent_schemas.DraftIntentRequest(
        portfolio_type="long_term_growth",
        risk_tolerance="moderate",
    )
    assert request.portfolio_type == "long_term_growth"

    for invalid in (
        {"destination": "trade"},
        {"portfolio_type": "Growth at any cost"},
        {"risk_tolerance": "Extreme"},
        {"additional_context": "unsafe\u0001context"},
        {"unsupported": "value"},
        {
            "clarification_round": 1,
            "clarifications": {"objective": "Growth"},
        },
        {
            "clarification_round": 1,
            "prior_questions": [{"id": "objective", "question": "What objective?"}],
            "clarifications": {"different_id": "Growth"},
        },
    ):
        with pytest.raises(ValidationError):
            portfolio_intent_schemas.DraftIntentRequest.model_validate(invalid)


@pytest.mark.parametrize(
    "intent",
    [
        "<script>alert(1)</script>",
        "- Growth objective\n- Income objective",
        "One.\n\nTwo.\n\nThree.\n\nFour.",
        "Growth objective.\n\nWould you clarify the target market?",
        "You are an expert portfolio manager. Return only the final portfolio.",
    ],
)
def test_completed_intent_requires_bounded_plain_text(intent: str) -> None:
    with pytest.raises(ValidationError):
        portfolio_intent_schemas.DraftIntentResponse(
            status="complete",
            intent=intent,
            questions=[],
            assumptions=[],
        )


@pytest.mark.asyncio
async def test_empty_request_returns_deterministic_questions_without_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = mock.AsyncMock()
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)

    result = await portfolio_intent.draft_portfolio_intent(
        _request(),
        portfolio_intent_schemas.DraftIntentRequest(),
        _user(),
    )

    assert result.status == "needs_clarification"
    assert [question.id for question in result.questions] == [
        "portfolio_objective",
        "target_market",
        "risk_and_horizon",
    ]
    assert result.assumptions == []
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_drafts_neutral_intent_with_strict_output(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = _complete_response()
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    settings = _task_settings()
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", settings)
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    body = portfolio_intent_schemas.DraftIntentRequest(
        market_country="ASX / Australia",
        portfolio_type="long_term_income",
        budget="AUD 100,000",
        risk_tolerance="moderate",
        holding_horizon="Multi-year",
        instrument_preference="ETFs and stocks",
        payout_frequency_preference="quarterly",
        market_specific_mechanics="Account for franking-credit eligibility as an unverified preference",
    )

    result = await portfolio_intent.draft_portfolio_intent(_request(), body, _user())

    assert result.status == "complete"
    assert result.intent == completion["intent"]
    assert result.assumptions == completion["assumptions"]
    execute.assert_awaited_once()
    call = execute.await_args
    assert call.args[:2] == (settings.client, settings.task)
    prompt = call.args[2]
    assert '"destination"' not in prompt
    assert "destination-neutral portfolio intent" in prompt
    assert "without assuming that holdings already exist" in prompt
    assert '"portfolio_type": "Long-term income"' in prompt
    assert '"payout_frequency_preference": "Quarterly"' in prompt
    assert "Never suppress factual disclosure" in prompt
    assert "workflows must verify" in prompt
    assert call.kwargs == {
        "response_json_schema": portfolio_intent_service.response_schema(),
        "schema_name": "portfolio_intent_draft",
    }


@pytest.mark.asyncio
async def test_initial_ai_call_can_return_targeted_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = _clarification_response()
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)

    result = await portfolio_intent.draft_portfolio_intent(
        _request(),
        portfolio_intent_schemas.DraftIntentRequest(portfolio_type="custom"),
        _user(),
    )

    assert result.status == "needs_clarification"
    assert [question.id for question in result.questions] == ["portfolio_objective", "target_market"]
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_followup_accumulates_answers_and_must_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    questions = [
        portfolio_intent_schemas.ClarificationQuestion(
            id="portfolio_objective",
            question="What is the primary objective?",
        ),
        portfolio_intent_schemas.ClarificationQuestion(
            id="target_market",
            question="Which market should the portfolio use?",
        ),
    ]
    completion = _complete_response(
        "Balanced | United States | Moderate risk | Multi-year.\n\n"
        "Emphasize quality growth, durable income, valuation, and material portfolio risks."
    )
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(completion)))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    body = portfolio_intent_schemas.DraftIntentRequest(
        clarification_round=1,
        prior_questions=questions,
        clarifications={
            "portfolio_objective": "Balanced growth and income",
            "target_market": "United States",
        },
    )

    result = await portfolio_intent.draft_portfolio_intent(_request(), body, _user())

    assert result.status == "complete"
    prompt = execute.await_args.args[2]
    assert '"portfolio_objective": "Balanced growth and income"' in prompt
    assert '"target_market": "United States"' in prompt
    assert "Do not ask another question" in prompt

    execute.return_value = ai.AIResponse(completion=json.dumps(_clarification_response()))
    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(_request(), body, _user())
    assert exc_info.value.status_code == 502
    assert "invalid portfolio intent" in exc_info.value.detail


def test_response_schema_is_recursive_strict() -> None:
    schema = portfolio_intent_service.response_schema()
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


@pytest.mark.asyncio
async def test_missing_task_and_provider_failures_are_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    body = portfolio_intent_schemas.DraftIntentRequest(portfolio_type="balanced")
    settings = types.SimpleNamespace(get_ai_client=lambda _task_id: None, tasks={})
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(_request(), body, _user())
    assert exc_info.value.status_code == 503
    assert "DRAFT_PORTFOLIO_INTENT" not in exc_info.value.detail

    execute = mock.AsyncMock(return_value=ai.AIResponse(success=False, error="secret provider detail"))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(_request(), body, _user())
    assert exc_info.value.status_code == 502
    assert "secret provider detail" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_timeout_and_disconnect_stop_request(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_execute(*_args: object, **_kwargs: object) -> ai.AIResponse:
        await asyncio.sleep(1)
        return ai.AIResponse(completion=json.dumps(_complete_response()))

    body = portfolio_intent_schemas.DraftIntentRequest(portfolio_type="balanced")
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", slow_execute)
    monkeypatch.setattr(portfolio_intent, "_TASK_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(_request(), body, _user())
    assert exc_info.value.status_code == 504

    execute = mock.AsyncMock()
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    with pytest.raises(HTTPException) as exc_info:
        await portfolio_intent.draft_portfolio_intent(_request(disconnected=True), body, _user())
    assert exc_info.value.status_code == 499
    execute.assert_not_awaited()


def test_endpoint_accepts_sparse_request_and_rejects_unknown_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion=json.dumps(_clarification_response())))
    monkeypatch.setattr(portfolio_intent.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(portfolio_intent.ai, "execute_task_prompt", execute)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.post("/portfolio-intent/draft", json={})
    removed_destination = client.post("/portfolio-intent/draft", json={"destination": "build"})
    invalid_response = client.post("/portfolio-intent/draft", json={"unsupported": "value"})

    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert removed_destination.status_code == 422
    assert invalid_response.status_code == 422


def test_task_policy_uses_luna_low_without_web_search() -> None:
    task = app_config.ai_task_settings.tasks["DRAFT_PORTFOLIO_INTENT"]

    assert task.vendor == "AzureOpenAI"
    assert task.tier == "LowCost"
    assert task.model == "gpt-5.6-luna"
    assert task.reasoning_level == "low"
    assert task.web_search is False
