import json
import types
from unittest import mock

import pytest
from sse_starlette import sse
from starlette.requests import Request

from app.routers import ipo_analyzer
from app.utils import ai


def test_low_cost_prompt_knows_prospectus_exists_without_receiving_content() -> None:
    prompt = ipo_analyzer._build_prompt_request(
        "Example Limited",
        "ASX listing",
        has_prospectus=True,
    )

    assert "Uploaded Prospectus: Available" in prompt
    assert "content is intentionally excluded" in prompt
    assert "SECRET PROSPECTUS CONTENT" not in prompt


def test_prospectus_is_appended_after_generated_prompt_with_source_boundary() -> None:
    generated_prompt = "Analyze Example Limited."
    markdown = "# SECRET PROSPECTUS CONTENT"

    premium_prompt = ipo_analyzer._append_prospectus(generated_prompt, markdown)

    assert premium_prompt.startswith(generated_prompt)
    assert premium_prompt.index("## Uploaded Prospectus") > premium_prompt.index(generated_prompt)
    assert premium_prompt.index(markdown) > premium_prompt.index("## Uploaded Prospectus")
    assert "untrusted source material" in premium_prompt


async def _collect_stream_events(response: sse.EventSourceResponse) -> list[dict[str, object]]:
    return [json.loads(event["data"]) async for event in response.body_iterator]


def _task_settings() -> types.SimpleNamespace:
    task = types.SimpleNamespace(model="test-model", temperature=0.1)
    return types.SimpleNamespace(
        get_ai_client=lambda _task_id: object(),
        tasks={
            "IPO_ANALYZER_BUILD_PROMPT": task,
            "IPO_ANALYZER_ANALYZE": task,
        },
    )


@pytest.mark.asyncio
async def test_stream_appends_converted_prospectus_only_to_premium_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated premium instructions"),
            ai.AIResponse(success=True, completion="Analysis result"),
        ]
    )
    delete_pdf = mock.Mock()
    monkeypatch.setattr(ipo_analyzer.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(ipo_analyzer.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(ipo_analyzer.prospectus, "delete_pdf", delete_pdf)
    monkeypatch.setattr(
        ipo_analyzer.prospectus,
        "convert_pdf_to_markdown",
        mock.AsyncMock(return_value="# Confidential Prospectus"),
    )

    response = await ipo_analyzer.ipo_analyzer_stream(
        Request({"type": "http", "method": "GET", "path": "/analyze-ipo/stream", "headers": []}),
        company_name="Example Limited",
        additional_notes="ASX listing",
        prospectus_id="a" * 32,
        user={},
    )
    events = await _collect_stream_events(response)

    low_cost_prompt = execute_prompt.await_args_list[0].args[2]
    premium_prompt = execute_prompt.await_args_list[1].args[2]
    assert "# Confidential Prospectus" not in low_cost_prompt
    assert "Uploaded Prospectus: Available" in low_cost_prompt
    assert premium_prompt.endswith("# Confidential Prospectus\n</uploaded_prospectus_markdown>")
    assert events[1] == {
        "type": "progress",
        "step": 2,
        "total": 5,
        "message": "Converting prospectus PDF to Markdown...",
    }
    assert events[-2]["step"] == events[-2]["total"] == 5
    delete_pdf.assert_called_once_with("a" * 32)


@pytest.mark.asyncio
async def test_stream_warns_and_continues_when_conversion_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_prompt = mock.AsyncMock(
        side_effect=[
            ai.AIResponse(success=True, completion="Generated premium instructions"),
            ai.AIResponse(success=True, completion="Analysis result"),
        ]
    )
    delete_pdf = mock.Mock()
    monkeypatch.setattr(ipo_analyzer.config, "ai_task_settings", _task_settings())
    monkeypatch.setattr(ipo_analyzer.ai, "execute_prompt", execute_prompt)
    monkeypatch.setattr(ipo_analyzer.prospectus, "delete_pdf", delete_pdf)
    monkeypatch.setattr(
        ipo_analyzer.prospectus,
        "convert_pdf_to_markdown",
        mock.AsyncMock(side_effect=ipo_analyzer.prospectus.ProspectusConversionError("conversion failed")),
    )

    response = await ipo_analyzer.ipo_analyzer_stream(
        Request({"type": "http", "method": "GET", "path": "/analyze-ipo/stream", "headers": []}),
        company_name="Example Limited",
        additional_notes="",
        prospectus_id="b" * 32,
        user={},
    )
    events = await _collect_stream_events(response)

    assert any(event["type"] == "warning" for event in events)
    assert events[-1] == {"type": "result", "content": "Analysis result"}
    assert "Uploaded Prospectus" not in execute_prompt.await_args_list[1].args[2]
    delete_pdf.assert_called_once_with("b" * 32)
