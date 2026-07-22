"""Utility for executing prompts against AI/LLM clients."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default sampling temperature used when a task does not configure one explicitly.
DEFAULT_TEMPERATURE = 0.1


def _is_debug_mode() -> bool:
    return os.getenv("LLM_DEBUG_MODE", "").lower() in ("1", "true", "yes")


@dataclass
class AIResponse:
    success: bool = True
    completion: str = ""
    error: str = ""
    token_usage_input: int = 0
    token_usage_output: int = 0
    token_usage_total: int = 0


async def execute_prompt(
    client,
    model: str,
    prompt: str,
    temperature: float | None = None,
    *,
    response_json_schema: Mapping[str, object] | None = None,
    schema_name: str = "structured_response",
    enable_web_search: bool = True,
) -> AIResponse:
    """Execute a prompt against an AI client and return the result.

    Supports:
    - google.genai.Client (Gemini)
    - openai.AsyncOpenAI / AsyncAzureOpenAI (OpenAI-compatible)

    Web search is enabled where supported. ``temperature`` controls sampling; when
    None, ``DEFAULT_TEMPERATURE`` is used.
    """
    from google import genai
    from openai import AsyncOpenAI

    resolved_temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
    vendor = type(client).__name__
    logger.info("Executing prompt | vendor=%s | model=%s | temperature=%.2f", vendor, model, resolved_temperature)
    if _is_debug_mode():
        logger.debug("Prompt text:\n%s", prompt)

    start_time = time.time()

    try:
        if isinstance(client, genai.Client):
            result = await _execute_gemini(
                client,
                model,
                prompt,
                resolved_temperature,
                response_json_schema=response_json_schema,
                enable_web_search=enable_web_search,
            )
        elif isinstance(client, AsyncOpenAI):
            result = await _execute_openai(
                client,
                model,
                prompt,
                resolved_temperature,
                response_json_schema=response_json_schema,
                schema_name=schema_name,
                enable_web_search=enable_web_search,
            )
        else:
            result = AIResponse(success=False, error=f"Unsupported client type: {vendor}")
    except Exception as e:
        result = AIResponse(success=False, error=str(e))

    elapsed = time.time() - start_time

    if result.success:
        logger.info(
            "Prompt completed | vendor=%s | model=%s | duration=%.2fs | tokens_in=%d | tokens_out=%d | tokens_total=%d",
            vendor,
            model,
            elapsed,
            result.token_usage_input,
            result.token_usage_output,
            result.token_usage_total,
        )
    else:
        logger.info(
            "Prompt failed | vendor=%s | model=%s | duration=%.2fs | error=%s",
            vendor,
            model,
            elapsed,
            result.error,
        )

    if _is_debug_mode() and result.completion:
        logger.debug("Completion text:\n%s", result.completion)

    return result


async def _execute_gemini(
    client,
    model: str,
    prompt: str,
    temperature: float,
    *,
    response_json_schema: Mapping[str, object] | None,
    enable_web_search: bool,
) -> AIResponse:
    """Execute prompt using Google Gemini client with grounding (web search)."""
    from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

    config_kwargs: dict[str, object] = {"temperature": temperature}
    if enable_web_search:
        config_kwargs["tools"] = [Tool(google_search=GoogleSearch())]
    if response_json_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = dict(response_json_schema)
    config = GenerateContentConfig(**config_kwargs)

    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )

    token_usage_input = 0
    token_usage_output = 0
    token_usage_total = 0
    if response.usage_metadata:
        token_usage_input = response.usage_metadata.prompt_token_count or 0
        token_usage_output = response.usage_metadata.candidates_token_count or 0
        token_usage_total = response.usage_metadata.total_token_count or 0

    return AIResponse(
        success=True,
        completion=response.text,
        token_usage_input=token_usage_input,
        token_usage_output=token_usage_output,
        token_usage_total=token_usage_total,
    )


async def _execute_openai(
    client,
    model: str,
    prompt: str,
    temperature: float,
    *,
    response_json_schema: Mapping[str, object] | None,
    schema_name: str,
    enable_web_search: bool,
) -> AIResponse:
    """Execute prompt using OpenAI-compatible client with web search."""
    base_url = str(client.base_url) if client.base_url else ""
    is_openrouter = "openrouter" in base_url.lower()

    if is_openrouter:
        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if enable_web_search:
            request_kwargs["extra_body"] = {"plugins": [{"id": "web"}]}
        if response_json_schema is not None:
            request_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(response_json_schema),
                },
            }
        response = await client.chat.completions.create(**request_kwargs)
    else:
        request_kwargs = {
            "model": model,
            "input": prompt,
            "temperature": temperature,
        }
        if enable_web_search:
            request_kwargs["tools"] = [{"type": "web_search_preview"}]
        if response_json_schema is not None:
            request_kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(response_json_schema),
                }
            }
        response = await client.responses.create(**request_kwargs)

    completion = ""
    token_usage_input = 0
    token_usage_output = 0
    token_usage_total = 0

    if is_openrouter:
        if response.choices and response.choices[0].message:
            completion = response.choices[0].message.content
        if response.usage:
            token_usage_input = response.usage.prompt_tokens or 0
            token_usage_output = response.usage.completion_tokens or 0
            token_usage_total = response.usage.total_tokens or 0
    else:
        completion = response.output_text or ""
        if response.usage:
            token_usage_input = response.usage.input_tokens or 0
            token_usage_output = response.usage.output_tokens or 0
            token_usage_total = token_usage_input + token_usage_output

    return AIResponse(
        success=True,
        completion=completion,
        token_usage_input=token_usage_input,
        token_usage_output=token_usage_output,
        token_usage_total=token_usage_total,
    )
