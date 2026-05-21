"""Service for fetching and caching latest market news via AI/LLM."""

from __future__ import annotations

import json
import logging
import random
import time

logger = logging.getLogger(__name__)

REDIS_KEY_SUFFIX = "market_news:latest"
CACHE_TTL_SECONDS = 48 * 60 * 60  # 48 hours

SYSTEM_PROMPT_TEMPLATE = (
    "You are a financial news analyst. Provide the latest important stock market news "
    "relevant to the following market(s): {markets}. "
    "For each news item, include: a concise headline, a brief summary (2-3 sentences), "
    "the relevant market or region, an approximate date, and a URL link to the original source. "
    "Provide 10-15 news items covering diverse topics (earnings, policy, sector moves, IPOs, macro events). "
    'Return ONLY a JSON array of objects with keys: "headline", "summary", "market", "date", "url". '
    "No other text."
)


def _build_system_prompt() -> str:
    """Build the system prompt with dynamic market context."""
    from app.config import app_settings

    markets = app_settings.primary_markets or []
    if not markets or len(markets) == 0:
        markets_str = "global"
    else:
        markets_list = sorted(markets) + ["global"]
        markets_str = ", ".join(markets_list)
    return SYSTEM_PROMPT_TEMPLATE.format(markets=markets_str)


def _parse_news(raw: str) -> list[dict] | None:
    """Parse LLM response into a list of news items."""
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
        logger.warning("Failed to parse market news as JSON")
        return None

    if not isinstance(parsed, list):
        logger.warning("Market news response is not a list")
        return None

    required_keys = {"headline", "summary", "market", "date", "url"}
    valid_items = []
    for item in parsed:
        if isinstance(item, dict) and required_keys.issubset(item.keys()):
            valid_items.append(item)

    if not valid_items:
        logger.warning("No valid news items found in AI response")
        return None

    return valid_items


async def fetch_market_news() -> None:
    """Fetch latest market news via AI and cache in Redis if stale or missing."""
    from app.config import ai_task_settings, datastore_settings

    if not datastore_settings.redis_enabled or not datastore_settings.redis_client:
        logger.debug("Redis not available, skipping market news fetch")
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
                logger.info("Market news cache is fresh (age=%.1fh), skipping fetch", age_hours)
                return
    except Exception as exc:
        logger.warning("Failed to read market news cache: %s", exc)

    # Validate AI task config
    task_id = "DASHBOARD_FETCH_MARKET_NEWS"
    task_config = ai_task_settings.tasks.get(task_id)
    if not task_config:
        logger.warning("AI task '%s' is not configured, skipping market news fetch", task_id)
        return

    client = ai_task_settings.get_ai_client(task_id)
    if not client:
        logger.warning(
            "AI client for task '%s' could not be created (vendor=%s, tier=%s), skipping market news fetch",
            task_id,
            task_config.vendor,
            task_config.tier,
        )
        return

    # Fetch news
    logger.info("Fetching market news via AI task '%s'...", task_id)
    try:
        from app.utils.ai import execute_prompt

        result = await execute_prompt(client, task_config.model, _build_system_prompt())
    except Exception as exc:
        logger.error("Market news fetch failed: %s", exc)
        return

    if not result.success:
        logger.error("Market news fetch failed: %s", result.error)
        return

    news = _parse_news(result.completion)
    if not news:
        logger.error("Could not parse market news from AI response")
        return

    # Store to Redis
    try:
        payload = json.dumps({"generated_at": time.time(), "news": news})
        await redis.set(cache_key, payload)
        logger.info("Stored %d market news items to Redis", len(news))
    except Exception as exc:
        logger.error("Failed to store market news to Redis: %s", exc)


async def get_market_news() -> list[dict] | None:
    """Fetch cached market news from Redis.

    Returns None if Redis is unavailable or no cached news exist.
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
        news = data.get("news", [])
        if not news:
            return None
        random.shuffle(news)
        return news
    except Exception as exc:
        logger.warning("Failed to fetch market news from Redis: %s", exc)
        return None
