import datetime
import json
import logging
import re
from collections.abc import Callable

import redis.exceptions

from app import config
from app.utils import local_storage

DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
_FEATURE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
logger = logging.getLogger(__name__)


def result_cache_key(user: dict[str, object], feature: str) -> str:
    """Build a Redis result key that extends the corresponding local-storage namespace."""
    if not _FEATURE_PATTERN.fullmatch(feature):
        raise ValueError("Cache feature must be a valid local-storage feature name.")
    user_key = local_storage.derive_user_key(user)
    return f"{config.datastore_settings.key_prefix}{user_key}:{feature}:result"


def _parse_cached_result(
    value: str | bytes,
    *,
    input_fields: tuple[str, ...],
) -> dict[str, str] | None:
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    content = data.get("content")
    generated_at = data.get("generated_at")
    if not isinstance(content, str) or not content.strip() or not isinstance(generated_at, str):
        return None
    try:
        datetime.datetime.fromisoformat(generated_at)
    except ValueError:
        return None

    result = {
        "content": content,
        "generated_at": generated_at,
    }
    for field in input_fields:
        value = data.get(field)
        if not isinstance(value, str):
            return None
        result[field] = value
    return result


async def get_cached_result(
    user: dict[str, object],
    *,
    feature: str,
    input_fields: tuple[str, ...],
) -> dict[str, str] | None:
    """Return a validated cached result, evicting malformed entries."""
    settings = config.datastore_settings
    if not settings.redis_enabled or not settings.redis_client:
        return None

    key = result_cache_key(user, feature)
    try:
        value = await settings.redis_client.get(key)
    except redis.exceptions.RedisError as exc:
        logger.warning("Failed to read %s analysis cache from Redis: %s", feature, exc)
        return None

    if value is None:
        return None

    cached_result = _parse_cached_result(value, input_fields=input_fields)
    if cached_result is not None:
        return cached_result

    logger.warning("Removing invalid %s analysis cache entry: %s", feature, key)
    try:
        await settings.redis_client.delete(key)
    except redis.exceptions.RedisError as exc:
        logger.warning("Failed to remove invalid %s analysis cache entry: %s", feature, exc)
    return None


async def set_cached_result(
    user: dict[str, object],
    *,
    feature: str,
    inputs: dict[str, str],
    content: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """Cache a successful analysis result and its inputs with a bounded TTL."""
    if ttl_seconds <= 0:
        raise ValueError("Cache TTL must be greater than zero.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Cached analysis content must be a non-empty string.")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in inputs.items()):
        raise TypeError("Cached analysis inputs must be string key-value pairs.")

    settings = config.datastore_settings
    if not settings.redis_enabled or not settings.redis_client:
        return False

    data = {
        **inputs,
        "content": content,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    try:
        await settings.redis_client.set(
            result_cache_key(user, feature),
            json.dumps(data),
            ex=ttl_seconds,
        )
    except redis.exceptions.RedisError as exc:
        logger.warning("Failed to write %s analysis cache to Redis: %s", feature, exc)
        return False
    return True


def _parse_cached_payload(
    value: str | bytes,
    *,
    input_fields: tuple[str, ...],
    payload_validator: Callable[[object], bool],
) -> dict[str, object] | None:
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    generated_at = data.get("generated_at")
    payload = data.get("payload")
    if not isinstance(generated_at, str) or not isinstance(payload, dict):
        return None
    try:
        datetime.datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    try:
        payload_is_valid = payload_validator(payload)
    except (TypeError, ValueError):
        return None
    if payload_is_valid is not True:
        return None

    result: dict[str, object] = {
        "payload": payload,
        "generated_at": generated_at,
    }
    for field in input_fields:
        input_value = data.get(field)
        if not isinstance(input_value, str):
            return None
        result[field] = input_value
    return result


async def get_cached_payload(
    user: dict[str, object],
    *,
    feature: str,
    input_fields: tuple[str, ...],
    payload_validator: Callable[[object], bool],
) -> dict[str, object] | None:
    """Return a validated structured result, evicting malformed entries."""
    settings = config.datastore_settings
    if not settings.redis_enabled or not settings.redis_client:
        return None

    key = result_cache_key(user, feature)
    try:
        value = await settings.redis_client.get(key)
    except redis.exceptions.RedisError as exc:
        logger.warning("Failed to read %s structured analysis cache from Redis: %s", feature, exc)
        return None

    if value is None:
        return None

    cached_result = _parse_cached_payload(
        value,
        input_fields=input_fields,
        payload_validator=payload_validator,
    )
    if cached_result is not None:
        return cached_result

    logger.warning("Removing invalid %s structured analysis cache entry: %s", feature, key)
    try:
        await settings.redis_client.delete(key)
    except redis.exceptions.RedisError as exc:
        logger.warning("Failed to remove invalid %s structured analysis cache entry: %s", feature, exc)
    return None


async def set_cached_payload(
    user: dict[str, object],
    *,
    feature: str,
    inputs: dict[str, str],
    payload: dict[str, object],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """Cache a successful structured analysis result and its inputs with a bounded TTL."""
    if ttl_seconds <= 0:
        raise ValueError("Cache TTL must be greater than zero.")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in inputs.items()):
        raise TypeError("Cached analysis inputs must be string key-value pairs.")
    if not isinstance(payload, dict):
        raise TypeError("Cached analysis payload must be a dictionary.")

    data = {
        **inputs,
        "payload": payload,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    try:
        serialized = json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError("Cached analysis payload must be JSON-compatible.") from exc

    settings = config.datastore_settings
    if not settings.redis_enabled or not settings.redis_client:
        return False

    try:
        await settings.redis_client.set(
            result_cache_key(user, feature),
            serialized,
            ex=ttl_seconds,
        )
    except redis.exceptions.RedisError as exc:
        logger.warning("Failed to write %s structured analysis cache to Redis: %s", feature, exc)
        return False
    return True
