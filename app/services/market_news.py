"""Service for fetching and caching latest market news via AI/LLM."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time

logger = logging.getLogger(__name__)

REDIS_KEY_SUFFIX = "market_news:latest"
REDIS_KEY_ACTIONABLE_PROMPTS = "market_news:actionable_prompts"
REDIS_KEY_PROMPT_RESULT_PREFIX = "market_news:prompt_result:"
CACHE_TTL_SECONDS = 48 * 60 * 60  # 48 hours

ACTIONABLE_PROMPTS_TEMPLATE = (
    "You are a stock market research assistant. Based on the following market news, "
    "generate exactly 10 actionable prompts that users can use to explore investment opportunities "
    "or strategies driven by current market sentiment, trends, and events. "
    "The prompts should be relevant to the following market(s): {markets}. "
    "Each prompt must be specific, concise (1 sentence), and ready-to-use — no placeholders. "
    "Examples of good prompts: "
    '"Defensive portfolio during high inflation", '
    '"Buy-the-dip opportunities in tech after earnings miss", '
    '"High growth ETFs for 5-year horizon", '
    '"Top ASX dividend stocks under A$50". '
    "Return ONLY a JSON array of 10 strings, no other text.\n\n"
    "Market news:\n{news_summary}"
)

SYSTEM_PROMPT_TEMPLATE = (
    "You are a financial news analyst. Provide important stock market news from the last 30 days "
    "relevant to the following market(s): {markets}. "
    "Prioritize news that is local to the specified market(s) — domestic companies, local exchanges, "
    "and region-specific policy or economic events. Include global news only if it has direct impact "
    "on the specified market(s). "
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


def _build_actionable_prompts_prompt(news: list[dict]) -> str:
    """Build the prompt for generating actionable prompts from market news."""
    from app.config import app_settings

    markets = app_settings.primary_markets
    markets_str = ", ".join(sorted(markets)) if markets else "US"

    news_summary = "\n".join(f"- {item['headline']}: {item['summary']}" for item in news)
    return ACTIONABLE_PROMPTS_TEMPLATE.format(markets=markets_str, news_summary=news_summary)


def _parse_actionable_prompts(raw: str) -> list[str] | None:
    """Parse LLM response into a list of actionable prompt strings."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse actionable prompts as JSON")
        return None

    if not isinstance(parsed, list) or not all(isinstance(p, str) and p.strip() for p in parsed):
        logger.warning("Actionable prompts response is not a list of non-empty strings")
        return None

    return [p.strip() for p in parsed]


async def _generate_actionable_prompts(news: list[dict]) -> list[str] | None:
    """Generate actionable prompts based on market news via AI."""
    from app.config import ai_task_settings

    task_id = "DASHBOARD_GENERATE_ACTIONABLE_PROMPTS"
    task_config = ai_task_settings.tasks.get(task_id)
    if not task_config:
        logger.warning("AI task '%s' is not configured, skipping actionable prompt generation", task_id)
        return None

    client = ai_task_settings.get_ai_client(task_id)
    if not client:
        logger.warning(
            "AI client for task '%s' could not be created (vendor=%s, tier=%s), skipping actionable prompt generation",
            task_id,
            task_config.vendor,
            task_config.tier,
        )
        return None

    logger.info("Generating actionable prompts via AI task '%s'...", task_id)
    try:
        from app.utils import ai

        result = await ai.execute_task_prompt(client, task_config, _build_actionable_prompts_prompt(news))
    except Exception as exc:
        logger.error("Actionable prompt generation failed: %s", exc)
        return None

    if not result.success:
        logger.error("Actionable prompt generation failed: %s", result.error)
        return None

    return _parse_actionable_prompts(result.completion)


def _prompt_id(prompt: str) -> str:
    """Generate a short stable ID for a prompt using SHA-256."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]


EXECUTE_PROMPT_TEMPLATE = (
    "You are a concise stock market research assistant. "
    "Answer the following question in a short, actionable format. "
    "If the answer involves specific stocks or ETFs, list the tickers. "
    "Keep your reasoning to 2-3 sentences max.\n\n"
    "Question: {prompt}"
)


async def _execute_actionable_prompts(prompts: list[str]) -> None:
    """Execute each actionable prompt via AI and store results in Redis with TTL."""
    from app.config import ai_task_settings, datastore_settings

    task_id = "DASHBOARD_EXECUTE_ACTIONABLE_PROMPT"
    task_config = ai_task_settings.tasks.get(task_id)
    if not task_config:
        logger.warning("AI task '%s' is not configured, skipping prompt execution", task_id)
        return

    client = ai_task_settings.get_ai_client(task_id)
    if not client:
        logger.warning(
            "AI client for task '%s' could not be created (vendor=%s, tier=%s), skipping prompt execution",
            task_id,
            task_config.vendor,
            task_config.tier,
        )
        return

    redis = datastore_settings.redis_client
    prefix = datastore_settings.key_prefix

    from app.utils import ai

    for prompt in prompts:
        prompt_id = _prompt_id(prompt)
        cache_key = f"{prefix}{REDIS_KEY_PROMPT_RESULT_PREFIX}{prompt_id}"

        try:
            result = await ai.execute_task_prompt(
                client,
                task_config,
                EXECUTE_PROMPT_TEMPLATE.format(prompt=prompt),
            )
            if result.success and result.completion:
                payload = json.dumps({"prompt": prompt, "result": result.completion.strip()})
                await redis.set(cache_key, payload, ex=CACHE_TTL_SECONDS)
                logger.info("Stored result for prompt '%s' (id=%s)", prompt[:50], prompt_id)
            else:
                logger.warning("Failed to execute prompt '%s': %s", prompt[:50], result.error)
        except Exception as exc:
            logger.error("Error executing prompt '%s': %s", prompt[:50], exc)


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
        from app.utils import ai

        result = await ai.execute_task_prompt(client, task_config, _build_system_prompt())
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

    # Generate actionable prompts based on the news
    actionable_prompts = await _generate_actionable_prompts(news)
    if actionable_prompts:
        try:
            prompts_key = f"{datastore_settings.key_prefix}{REDIS_KEY_ACTIONABLE_PROMPTS}"
            await redis.set(prompts_key, json.dumps(actionable_prompts))
            logger.info("Stored %d actionable prompts to Redis", len(actionable_prompts))
        except Exception as exc:
            logger.error("Failed to store actionable prompts to Redis: %s", exc)

        # Execute each prompt and cache the results
        await _execute_actionable_prompts(actionable_prompts)


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


async def get_ai_ideas(count: int = 3) -> list[dict] | None:
    """Fetch random actionable prompts and their pre-computed results from Redis.

    Returns a list of {"prompt": ..., "result": ...} dicts, or None if unavailable.
    """
    from app.config import datastore_settings

    if not datastore_settings.redis_enabled or not datastore_settings.redis_client:
        return None

    redis = datastore_settings.redis_client
    prefix = datastore_settings.key_prefix

    try:
        prompts_key = f"{prefix}{REDIS_KEY_ACTIONABLE_PROMPTS}"
        cached = await redis.get(prompts_key)
        if not cached:
            return None

        prompts = json.loads(cached)
        if not prompts:
            return None

        selected = random.sample(prompts, min(count, len(prompts)))

        ideas = []
        for prompt in selected:
            prompt_id = _prompt_id(prompt)
            result_key = f"{prefix}{REDIS_KEY_PROMPT_RESULT_PREFIX}{prompt_id}"
            result_data = await redis.get(result_key)
            if result_data:
                data = json.loads(result_data)
                ideas.append({"prompt": data.get("prompt", prompt), "result": data.get("result", "")})

        return ideas if ideas else None
    except Exception as exc:
        logger.warning("Failed to fetch AI ideas from Redis: %s", exc)
        return None
