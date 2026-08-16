import json
import types
from unittest import mock

import pytest

from app import config
from app.schemas import sample_prompts as sample_prompt_schemas
from app.services import sample_prompts
from app.utils import ai


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    redis_client: types.SimpleNamespace,
) -> types.SimpleNamespace:
    task = types.SimpleNamespace(
        vendor="AzureOpenAI",
        tier="LowCost",
        model="gpt-5.6-luna",
        web_search=False,
        reasoning_level="low",
    )
    client = object()
    monkeypatch.setattr(
        config,
        "datastore_settings",
        types.SimpleNamespace(
            redis_enabled=True,
            redis_client=redis_client,
            key_prefix="al:",
        ),
    )
    monkeypatch.setattr(
        config,
        "ai_task_settings",
        types.SimpleNamespace(
            tasks={"DASHBOARD_GENERATE_SAMPLE_PROMPTS": task},
            get_ai_client=lambda _task_id: client,
        ),
    )
    monkeypatch.setattr(
        config,
        "app_settings",
        types.SimpleNamespace(primary_markets={"US", "AU"}),
    )
    return types.SimpleNamespace(task=task, client=client)


def _prompts(count: int) -> list[str]:
    return [f"Research sample company {index} for a five year investment horizon" for index in range(count)]


def test_build_prompt_limits_model_to_prompt_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "app_settings",
        types.SimpleNamespace(primary_markets={"US", "AU"}),
    )

    prompt = sample_prompts._build_system_prompt()

    assert "sample-prompt writer" in prompt
    assert "Do not perform research" in prompt
    assert "Generate around 20" in prompt
    assert '"markets": [' in prompt
    assert prompt.index('"AU"') < prompt.index('"US"')


def test_normalize_prompts_deduplicates_filters_and_keeps_at_most_twenty() -> None:
    values: list[object] = [
        "  Analyze   AAPL for long-term growth  ",
        "analyze aapl for long-term growth",
        "Unsafe\nprompt",
        "",
        42,
        "x" * (sample_prompts.MAX_PROMPT_LENGTH + 1),
        " ".join(["word"] * (sample_prompts.MAX_PROMPT_WORDS + 1)),
        *_prompts(25),
    ]

    normalized = sample_prompts._normalize_prompts(values)

    assert normalized[0] == "Analyze AAPL for long-term growth"
    assert len(normalized) == sample_prompts.MAX_PROMPTS
    assert len({prompt.casefold() for prompt in normalized}) == len(normalized)
    assert all("\n" not in prompt for prompt in normalized)


def test_parse_prompts_accepts_approximately_twenty_structured_prompts() -> None:
    values = _prompts(12)

    parsed = sample_prompts._parse_prompts(json.dumps({"prompts": values}))

    assert parsed == values
    assert sample_prompts._parse_prompts(json.dumps(values)) is None


@pytest.mark.asyncio
async def test_generation_skips_fresh_valid_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_000_000.0
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(
            return_value=json.dumps(
                {
                    "generated_at": now - 60,
                    "prompts": _prompts(10),
                }
            )
        ),
        set=mock.AsyncMock(),
    )
    _configure(monkeypatch, redis_client)
    execute = mock.AsyncMock()
    monkeypatch.setattr(ai, "execute_task_prompt", execute)
    monkeypatch.setattr(sample_prompts.time, "time", lambda: now)

    await sample_prompts.generate_sample_prompts()

    execute.assert_not_awaited()
    redis_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_normalizes_and_caches_at_most_twenty_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(return_value=None),
        set=mock.AsyncMock(),
    )
    settings = _configure(monkeypatch, redis_client)
    generated = [
        "  Analyze   AAPL for long-term growth  ",
        "analyze aapl for long-term growth",
        *_prompts(25),
    ]
    execute = mock.AsyncMock(
        return_value=ai.AIResponse(
            completion=json.dumps({"prompts": generated}),
        )
    )
    monkeypatch.setattr(ai, "execute_task_prompt", execute)
    monkeypatch.setattr(sample_prompts.time, "time", lambda: now)

    await sample_prompts.generate_sample_prompts()

    execute.assert_awaited_once()
    call = execute.await_args
    assert call.args[:2] == (settings.client, settings.task)
    assert call.kwargs == {
        "response_json_schema": sample_prompt_schemas.SamplePromptBatch.model_json_schema(),
        "schema_name": "dashboard_sample_prompts",
    }
    redis_client.set.assert_awaited_once()
    cache_key, raw_payload = redis_client.set.await_args.args
    payload = json.loads(raw_payload)
    assert cache_key == "al:dashboard:sample_prompts"
    assert payload["generated_at"] == now
    assert len(payload["prompts"]) == sample_prompts.MAX_PROMPTS
    assert len({prompt.casefold() for prompt in payload["prompts"]}) == sample_prompts.MAX_PROMPTS


@pytest.mark.asyncio
async def test_invalid_generation_does_not_replace_cached_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(
            return_value=json.dumps(
                {
                    "generated_at": 1.0,
                    "prompts": _prompts(10),
                }
            )
        ),
        set=mock.AsyncMock(),
    )
    _configure(monkeypatch, redis_client)
    monkeypatch.setattr(
        ai,
        "execute_task_prompt",
        mock.AsyncMock(return_value=ai.AIResponse(completion='{"prompts":[]}')),
    )
    monkeypatch.setattr(sample_prompts.time, "time", lambda: sample_prompts.CACHE_TTL_SECONDS + 2.0)

    await sample_prompts.generate_sample_prompts()

    redis_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrieval_revalidates_cached_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    cached_values: list[object] = [
        "Analyze AAPL",
        "analyze aapl",
        "Compare MSFT and GOOGL",
        "Unsafe\tprompt",
        42,
    ]
    redis_client = types.SimpleNamespace(
        get=mock.AsyncMock(
            return_value=json.dumps(
                {
                    "generated_at": 1_000_000.0,
                    "prompts": cached_values,
                }
            )
        ),
    )
    _configure(monkeypatch, redis_client)
    monkeypatch.setattr(sample_prompts.random, "sample", lambda population, count: population[:count])

    prompts = await sample_prompts.get_random_sample_prompts(4)

    assert prompts == ["Analyze AAPL", "Compare MSFT and GOOGL"]


@pytest.mark.asyncio
async def test_retrieval_returns_none_for_invalid_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = types.SimpleNamespace(get=mock.AsyncMock(return_value='{"prompts":"invalid"}'))
    _configure(monkeypatch, redis_client)

    prompts = await sample_prompts.get_random_sample_prompts()

    assert prompts is None
