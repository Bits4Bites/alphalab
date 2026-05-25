"""Unit tests for app.utils.ai module."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.ai import AIResponse, _is_debug_mode, execute_prompt

# --- Fixtures and helpers ---


@dataclass
class FakeUsageMetadata:
    prompt_token_count: int = 10
    candidates_token_count: int = 20
    total_token_count: int = 30


@dataclass
class FakeGeminiResponse:
    text: str = "Gemini says hello"
    usage_metadata: FakeUsageMetadata | None = None


@dataclass
class FakeOpenAIUsage:
    prompt_tokens: int = 15
    completion_tokens: int = 25
    total_tokens: int = 40


@dataclass
class FakeMessage:
    content: str = "OpenAI says hello"


@dataclass
class FakeChoice:
    message: FakeMessage | None = None


@dataclass
class FakeOpenRouterResponse:
    choices: list | None = None
    usage: FakeOpenAIUsage | None = None


@dataclass
class FakeResponsesUsage:
    input_tokens: int = 12
    output_tokens: int = 18


@dataclass
class FakeResponsesResponse:
    output_text: str = "Responses API says hello"
    usage: FakeResponsesUsage | None = None


# --- Tests for _is_debug_mode ---


class TestIsDebugMode:
    def test_true_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("1", "true", "True", "TRUE", "yes", "YES"):
            monkeypatch.setenv("LLM_DEBUG_MODE", val)
            assert _is_debug_mode() is True

    def test_false_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("0", "false", "no", "", "random"):
            monkeypatch.setenv("LLM_DEBUG_MODE", val)
            assert _is_debug_mode() is False

    def test_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_DEBUG_MODE", raising=False)
        assert _is_debug_mode() is False


# --- Tests for AIResponse dataclass ---


class TestAIResponse:
    def test_defaults(self) -> None:
        r = AIResponse()
        assert r.success is True
        assert r.completion == ""
        assert r.error == ""
        assert r.token_usage_input == 0
        assert r.token_usage_output == 0
        assert r.token_usage_total == 0

    def test_custom_values(self) -> None:
        r = AIResponse(success=False, completion="hi", error="oops", token_usage_total=99)
        assert r.success is False
        assert r.completion == "hi"
        assert r.error == "oops"
        assert r.token_usage_total == 99


# --- Tests for execute_prompt ---


class TestExecutePromptGemini:
    @pytest.fixture
    def gemini_client(self) -> MagicMock:
        """Create a mock that passes isinstance(client, genai.Client) check."""
        from google.genai import Client

        client = MagicMock(spec=Client)
        return client

    @pytest.mark.asyncio
    async def test_success_with_usage(self, gemini_client: MagicMock) -> None:
        fake_response = FakeGeminiResponse(
            text="analysis result",
            usage_metadata=FakeUsageMetadata(prompt_token_count=5, candidates_token_count=15, total_token_count=20),
        )
        gemini_client.aio.models.generate_content = AsyncMock(return_value=fake_response)

        result = await execute_prompt(gemini_client, "gemini-2.0-flash", "Analyze AAPL")

        assert result.success is True
        assert result.completion == "analysis result"
        assert result.token_usage_input == 5
        assert result.token_usage_output == 15
        assert result.token_usage_total == 20

    @pytest.mark.asyncio
    async def test_success_without_usage(self, gemini_client: MagicMock) -> None:
        fake_response = FakeGeminiResponse(text="no usage", usage_metadata=None)
        gemini_client.aio.models.generate_content = AsyncMock(return_value=fake_response)

        result = await execute_prompt(gemini_client, "gemini-2.0-flash", "test")

        assert result.success is True
        assert result.completion == "no usage"
        assert result.token_usage_input == 0
        assert result.token_usage_output == 0
        assert result.token_usage_total == 0

    @pytest.mark.asyncio
    async def test_exception_returns_error(self, gemini_client: MagicMock) -> None:
        gemini_client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("API down"))

        result = await execute_prompt(gemini_client, "gemini-2.0-flash", "test")

        assert result.success is False
        assert "API down" in result.error


class TestExecutePromptOpenAI:
    @pytest.fixture
    def openai_client(self) -> MagicMock:
        """Mock that passes isinstance(client, AsyncOpenAI) check."""
        from openai import AsyncOpenAI

        client = MagicMock(spec=AsyncOpenAI)
        client.base_url = "https://api.openai.com/v1"
        return client

    @pytest.fixture
    def openrouter_client(self) -> MagicMock:
        """Mock for OpenRouter (OpenAI-compatible with different base_url)."""
        from openai import AsyncOpenAI

        client = MagicMock(spec=AsyncOpenAI)
        client.base_url = "https://openrouter.ai/api/v1"
        return client

    @pytest.mark.asyncio
    async def test_responses_api_success(self, openai_client: MagicMock) -> None:
        fake_response = FakeResponsesResponse(
            output_text="OpenAI result",
            usage=FakeResponsesUsage(input_tokens=10, output_tokens=20),
        )
        openai_client.responses.create = AsyncMock(return_value=fake_response)

        result = await execute_prompt(openai_client, "gpt-4o", "Analyze MSFT")

        assert result.success is True
        assert result.completion == "OpenAI result"
        assert result.token_usage_input == 10
        assert result.token_usage_output == 20
        assert result.token_usage_total == 30

    @pytest.mark.asyncio
    async def test_responses_api_no_usage(self, openai_client: MagicMock) -> None:
        fake_response = FakeResponsesResponse(output_text="result", usage=None)
        openai_client.responses.create = AsyncMock(return_value=fake_response)

        result = await execute_prompt(openai_client, "gpt-4o", "test")

        assert result.success is True
        assert result.token_usage_total == 0

    @pytest.mark.asyncio
    async def test_openrouter_chat_completions(self, openrouter_client: MagicMock) -> None:
        fake_response = FakeOpenRouterResponse(
            choices=[FakeChoice(message=FakeMessage(content="router result"))],
            usage=FakeOpenAIUsage(prompt_tokens=8, completion_tokens=12, total_tokens=20),
        )
        openrouter_client.chat.completions.create = AsyncMock(return_value=fake_response)

        result = await execute_prompt(openrouter_client, "anthropic/claude-3", "test")

        assert result.success is True
        assert result.completion == "router result"
        assert result.token_usage_input == 8
        assert result.token_usage_output == 12
        assert result.token_usage_total == 20

    @pytest.mark.asyncio
    async def test_openrouter_empty_choices(self, openrouter_client: MagicMock) -> None:
        fake_response = FakeOpenRouterResponse(choices=[], usage=None)
        openrouter_client.chat.completions.create = AsyncMock(return_value=fake_response)

        result = await execute_prompt(openrouter_client, "model", "test")

        assert result.success is True
        assert result.completion == ""

    @pytest.mark.asyncio
    async def test_exception_returns_error(self, openai_client: MagicMock) -> None:
        openai_client.responses.create = AsyncMock(side_effect=ValueError("bad request"))

        result = await execute_prompt(openai_client, "gpt-4o", "test")

        assert result.success is False
        assert "bad request" in result.error


class TestExecutePromptUnsupportedClient:
    @pytest.mark.asyncio
    async def test_unsupported_client_type(self) -> None:
        client = object()

        result = await execute_prompt(client, "some-model", "test prompt")

        assert result.success is False
        assert "Unsupported client type" in result.error


class TestExecutePromptDebugLogging:
    @pytest.mark.asyncio
    async def test_debug_mode_logs_prompt(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        """When LLM_DEBUG_MODE=1, prompt and completion are logged at DEBUG level."""
        monkeypatch.setenv("LLM_DEBUG_MODE", "1")

        from google.genai import Client

        client = MagicMock(spec=Client)
        fake_response = FakeGeminiResponse(text="debug output", usage_metadata=None)
        client.aio.models.generate_content = AsyncMock(return_value=fake_response)

        with caplog.at_level("DEBUG", logger="app.utils.ai"):
            result = await execute_prompt(client, "model", "my secret prompt")

        assert result.success is True
        assert "my secret prompt" in caplog.text
        assert "debug output" in caplog.text
