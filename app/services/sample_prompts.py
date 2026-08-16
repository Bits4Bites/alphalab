"""Service for generating and caching sample dashboard prompts."""

from __future__ import annotations

import json
import logging
import random
import time

logger = logging.getLogger(__name__)

REDIS_KEY_SUFFIX = "dashboard:sample_prompts"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

SYSTEM_PROMPT_TEMPLATE = (
    "You are a stock market research assistant for a platform called AlphaLab. "
    "Generate 20 diverse sample prompts that users might ask. "
    "The prompts should be relevant to the following market(s): {markets}. "
    "Cover topics like stock analysis, portfolio strategy, sector trends, earnings, "
    "technical indicators, risk assessment, and market news. "
    "Each prompt must be specific and actionable - use real ticker symbols, company names, "
    "and concrete numbers. Do NOT use placeholders like 'e.g.', 'such as', or 'for a given stock'. "
    "Each prompt should be a concise, ready-to-use question or request (max 20 words). "
    "Return ONLY a JSON array of 20 strings, no other text."
)


def _build_system_prompt() -> str:
    """Build the system prompt with dynamic market context."""
    from app.config import app_settings

    markets = app_settings.primary_markets
    if not markets:
        markets_str = "US"
    else:
        markets_str = ", ".join(sorted(markets))
    return SYSTEM_PROMPT_TEMPLATE.format(markets=markets_str)


def _parse_prompts(raw: str) -> list[str] | None:
    """Parse LLM response into a list of prompt strings, handling common formatting quirks."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse sample prompts as JSON")
        return None

    if not isinstance(parsed, list) or not all(isinstance(p, str) and p.strip() for p in parsed):
        logger.warning("Sample prompts response is not a list of non-empty strings")
        return None

    return [p.strip() for p in parsed]


async def generate_sample_prompts() -> None:
    """Generate sample prompts via AI and cache in Redis if stale or missing."""
    from app.config import ai_task_settings, datastore_settings

    if not datastore_settings.redis_enabled or not datastore_settings.redis_client:
        logger.debug("Redis not available, skipping sample prompt generation")
        return

    redis = datastore_settings.redis_client
    cache_key = f"{datastore_settings.key_prefix}{REDIS_KEY_SUFFIX}"

    # Check existing cache
    try:
        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            generated_at = data.get("generated_at", 0)
            if time.time() - generated_at < CACHE_TTL_SECONDS:
                age_hours = (time.time() - generated_at) / 3600
                logger.info("Sample prompts cache is fresh (age=%.1fh), skipping generation", age_hours)
                return
    except Exception as exc:
        logger.warning("Failed to read sample prompts cache: %s", exc)

    # Validate AI task config
    task_id = "DASHBOARD_GENERATE_SAMPLE_PROMPTS"
    task_config = ai_task_settings.tasks.get(task_id)
    if not task_config:
        logger.warning("AI task '%s' is not configured, skipping sample prompt generation", task_id)
        return

    client = ai_task_settings.get_ai_client(task_id)
    if not client:
        logger.warning(
            "AI client for task '%s' could not be created (vendor=%s, tier=%s), skipping sample prompt generation",
            task_id,
            task_config.vendor,
            task_config.tier,
        )
        return

    # Generate prompts
    logger.info("Generating sample prompts via AI task '%s'...", task_id)
    try:
        from app.utils import ai

        result = await ai.execute_task_prompt(client, task_config, _build_system_prompt())
    except Exception as exc:
        logger.error("Sample prompt generation failed: %s", exc)
        return

    if not result.success:
        logger.error("Sample prompt generation failed: %s", result.error)
        return

    prompts = _parse_prompts(result.completion)
    if not prompts:
        logger.error("Could not parse sample prompts from AI response")
        return

    # Store to Redis
    try:
        payload = json.dumps({"generated_at": time.time(), "prompts": prompts})
        await redis.set(cache_key, payload)
        logger.info("Stored %d sample prompts to Redis", len(prompts))
    except Exception as exc:
        logger.error("Failed to store sample prompts to Redis: %s", exc)


async def get_random_sample_prompts(count: int = 4) -> list[str] | None:
    """Fetch cached sample prompts from Redis and return a random subset.

    Returns None if Redis is unavailable or no cached prompts exist.
    """
    from app.config import datastore_settings

    if not datastore_settings.redis_enabled or not datastore_settings.redis_client:
        return None

    redis = datastore_settings.redis_client
    cache_key = f"{datastore_settings.key_prefix}{REDIS_KEY_SUFFIX}"

    try:
        cached = await redis.get(cache_key)
        if not cached:
            return None
        data = json.loads(cached)
        prompts = data.get("prompts", [])
        if not prompts:
            return None
        return random.sample(prompts, min(count, len(prompts)))
    except Exception as exc:
        logger.warning("Failed to fetch sample prompts from Redis: %s", exc)
        return None
