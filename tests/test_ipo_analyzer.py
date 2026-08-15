import json
import types
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sse_starlette import sse
from starlette.requests import Request

from app.routers import ipo_analyzer
from app.services import auth
from app.utils import ai


def _user() -> dict[str, str]:
    return {
        "provider": "github",
        "sub": "987",
        "email": "ipo@example.com",
        "name": "IPO User",
        "avatar": "",
    }


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1, web_search=False, reasoning_level=None)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "IPO_ANALYZER_BUILD_PROMPT": task,
            "IPO_ANALYZER_ANALYZE": task,
        },
    )


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


@pytest.mark.asyncio
async def test_successful_stream_caches_inputs_without_prospectus(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated analysis prompt"),
            ai.AIResponse(success=True, completion="# IPO analysis"),
        ]
    )
    set_cached_result = mock.AsyncMock(return_value=True)
    delete_pdf = mock.Mock()
    monkeypatch.setattr(ipo_analyzer.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(ipo_analyzer.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(ipo_analyzer.analysis_cache, "set_cached_result", set_cached_result)
    monkeypatch.setattr(
        ipo_analyzer.prospectus,
        "convert_pdf_to_markdown",
        mock.AsyncMock(return_value="# Prospectus"),
    )
    monkeypatch.setattr(ipo_analyzer.prospectus, "delete_pdf", delete_pdf)

    response = await ipo_analyzer.ipo_analyzer_stream(
        Request({"type": "http", "method": "GET", "path": "/analyze-ipo/stream", "headers": []}),
        company_name=" Example Limited ",
        additional_notes=" ASX listing ",
        prospectus_id="c" * 32,
        user=_user(),
    )
    events = await _collect_stream_events(response)

    set_cached_result.assert_awaited_once_with(
        _user(),
        feature="ipo-analyzer",
        inputs={
            "company_name": "Example Limited",
            "additional_notes": "ASX listing",
        },
        content="# IPO analysis",
    )
    delete_pdf.assert_called_once_with("c" * 32)
    assert events[-1] == {"type": "result", "content": "# IPO analysis"}


def test_page_embeds_cached_result_and_browser_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cached_result = {
        "company_name": "Example Limited",
        "additional_notes": "ASX listing",
        "content": "# Cached IPO analysis",
        "generated_at": "2026-07-22T10:45:00+10:00",
    }
    get_cached_result = mock.AsyncMock(return_value=cached_result)
    monkeypatch.setattr(ipo_analyzer.analysis_cache, "get_cached_result", get_cached_result)
    client.cookies.set("access_token", auth.create_access_token(_user()))

    response = client.get("/analyze-ipo")

    assert response.status_code == 200
    get_cached_result.assert_awaited_once()
    cache_call = get_cached_result.await_args
    assert cache_call.args[0]["provider"] == "github"
    assert cache_call.args[0]["sub"] == "987"
    assert cache_call.kwargs == {
        "feature": "ipo-analyzer",
        "input_fields": ("company_name", "additional_notes"),
    }
    assert 'id="cached-result-data"' in response.text
    assert "# Cached IPO analysis" in response.text
    assert "This is a cached analysis completed on" in response.text
    assert "AlphaLabStorage.load" in response.text
    assert "AlphaLabStorage.save" in response.text
    assert "The file is not saved and must be selected again for each analysis." in response.text
    save_call = response.text.split("window.AlphaLabStorage.save(", maxsplit=1)[1].split(");", maxsplit=1)[0]
    assert "company_name: companyName" in save_call
    assert "additional_notes: additionalNotes" in save_call
    assert "prospectus" not in save_call
