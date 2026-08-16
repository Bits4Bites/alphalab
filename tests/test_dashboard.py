import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.routers import dashboard
from app.schemas import dashboard as dashboard_schemas
from app.services import auth
from app.utils import ai, local_storage


def _user() -> dict[str, str]:
    return {
        "sub": "123",
        "name": "Test User",
        "email": "test@example.com",
        "avatar": "",
        "provider": "github",
    }


def _task_settings() -> types.SimpleNamespace:
    build_task = types.SimpleNamespace(model="planner-model", web_search=False, reasoning_level="low")
    analyze_task = types.SimpleNamespace(model="analysis-model", web_search=True, reasoning_level="medium")
    build_client = object()
    analyze_client = object()
    clients = {
        "DASHBOARD_BUILD_PROMPT": build_client,
        "DASHBOARD_ANALYZE": analyze_client,
    }
    return types.SimpleNamespace(
        tasks={
            "DASHBOARD_BUILD_PROMPT": build_task,
            "DASHBOARD_ANALYZE": analyze_task,
        },
        clients=clients,
        get_ai_client=lambda task_id: clients.get(task_id),
    )


async def _collect_stream_events(response) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


def test_dashboard_redirects_when_unauthenticated(client: TestClient) -> None:
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_dashboard_renders_when_authenticated(client: TestClient) -> None:
    user_data = _user()
    token = auth.create_access_token(user_data)
    client.cookies.set("access_token", token)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Test User" in response.text
    assert "Analyze Ticker" in response.text
    assert "Compare Investments" in response.text
    assert 'href="/compare-investments"' in response.text
    assert "Build Portfolio" in response.text
    assert "Review Portfolio" in response.text
    storage_key = local_storage.derive_user_key(user_data)
    assert f'name="alphalab-storage-user-key" content="{storage_key}"' in response.text
    assert response.text.index("js/local-storage.js") < response.text.index("js/main.js")
    assert "new EventSource" not in response.text
    assert "fetch('/dashboard/stream'" in response.text
    assert "method: 'POST'" in response.text
    assert "renderSafeDashboardAnalysis(content" in response.text
    assert "sanitizeDashboardMarkdownNode" in response.text
    assert "renderSafeDashboardAnalysis(element.textContent, element)" in response.text


@pytest.mark.asyncio
async def test_dashboard_stream_uses_structured_plan_and_default_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _task_settings()
    planner_response = {
        "status": "accepted",
        "reason": None,
        "research_prompt": "Research current semiconductor valuations.",
        "disable_web_search": False,
    }
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(completion=json.dumps(planner_response)),
            ai.AIResponse(completion="# Semiconductor analysis"),
        ]
    )
    monkeypatch.setattr(dashboard.config, "ai_task_settings", settings)
    monkeypatch.setattr(dashboard.ai, "execute_task_prompt", execute)

    response = await dashboard.dashboard_stream(
        dashboard_schemas.DashboardAnalysisRequest(intent="Analyze semiconductor valuations"),
        _user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1] == {"type": "result", "content": "# Semiconductor analysis"}
    assert execute.await_count == 2
    planner_call, analysis_call = execute.await_args_list
    assert planner_call.args[:2] == (
        settings.clients["DASHBOARD_BUILD_PROMPT"],
        settings.tasks["DASHBOARD_BUILD_PROMPT"],
    )
    assert planner_call.kwargs["response_json_schema"] == dashboard_schemas.DashboardPlan.model_json_schema()
    assert planner_call.kwargs["schema_name"] == "dashboard_research_plan"
    assert "untrusted data" in planner_call.args[2]
    assert analysis_call.args[:2] == (
        settings.clients["DASHBOARD_ANALYZE"],
        settings.tasks["DASHBOARD_ANALYZE"],
    )
    assert "Trusted analysis instructions" in analysis_call.args[2]
    assert "authoritative sources" in analysis_call.args[2]
    assert "enable_web_search" not in analysis_call.kwargs


@pytest.mark.asyncio
async def test_dashboard_stream_allows_planner_to_disable_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(
                completion=json.dumps(
                    {
                        "status": "accepted",
                        "reason": None,
                        "research_prompt": "Explain the difference between stocks and bonds.",
                        "disable_web_search": True,
                    }
                )
            ),
            ai.AIResponse(completion="# Stocks and bonds"),
        ]
    )
    monkeypatch.setattr(dashboard.config, "ai_task_settings", settings)
    monkeypatch.setattr(dashboard.ai, "execute_task_prompt", execute)

    response = await dashboard.dashboard_stream(
        dashboard_schemas.DashboardAnalysisRequest(intent="What is the difference between stocks and bonds?"),
        _user=_user(),
    )
    events = await _collect_stream_events(response)

    assert events[-1]["type"] == "result"
    assert execute.await_args_list[1].kwargs == {"enable_web_search": False}


@pytest.mark.asyncio
async def test_dashboard_stream_stops_after_structured_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        return_value=ai.AIResponse(
            completion=json.dumps(
                {
                    "status": "rejected",
                    "reason": "The request is unrelated to financial markets.",
                    "research_prompt": None,
                    "disable_web_search": False,
                }
            )
        )
    )
    monkeypatch.setattr(dashboard.config, "ai_task_settings", settings)
    monkeypatch.setattr(dashboard.ai, "execute_task_prompt", execute)

    response = await dashboard.dashboard_stream(
        dashboard_schemas.DashboardAnalysisRequest(intent="Write a travel itinerary"),
        _user=_user(),
    )
    events = await _collect_stream_events(response)

    assert execute.await_count == 1
    assert events[-1] == {
        "type": "error",
        "message": (
            "Your request doesn't appear to be related to the stock market. "
            "The request is unrelated to financial markets."
        ),
    }


@pytest.mark.asyncio
async def test_dashboard_stream_rejects_invalid_planner_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(return_value=ai.AIResponse(completion="REJECTED: legacy response"))
    monkeypatch.setattr(dashboard.config, "ai_task_settings", settings)
    monkeypatch.setattr(dashboard.ai, "execute_task_prompt", execute)

    response = await dashboard.dashboard_stream(
        dashboard_schemas.DashboardAnalysisRequest(intent="Analyze AAPL"),
        _user=_user(),
    )
    events = await _collect_stream_events(response)

    assert execute.await_count == 1
    assert events[-1] == {
        "type": "error",
        "message": "The AI planner returned an invalid response. Please try again.",
    }


def test_dashboard_stream_is_post_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _task_settings()
    execute = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(
                completion=json.dumps(
                    {
                        "status": "accepted",
                        "reason": None,
                        "research_prompt": "Analyze AAPL.",
                        "disable_web_search": False,
                    }
                )
            ),
            ai.AIResponse(completion="# AAPL analysis"),
        ]
    )
    monkeypatch.setattr(dashboard.config, "ai_task_settings", settings)
    monkeypatch.setattr(dashboard.ai, "execute_task_prompt", execute)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    get_response = client.get("/dashboard/stream", params={"intent": "Analyze AAPL"})
    post_response = client.post("/dashboard/stream", json={"intent": " Analyze AAPL "})

    assert get_response.status_code == 405
    assert post_response.status_code == 200
    assert post_response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "result"' in post_response.text
    assert execute.await_count == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"intent": ""},
        {"intent": "x" * 301},
        {"intent": "Analyze AAPL", "unsupported": True},
    ],
)
def test_dashboard_stream_validates_request_body(client: TestClient, payload: dict[str, object]) -> None:
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.post("/dashboard/stream", json=payload)

    assert response.status_code == 422
