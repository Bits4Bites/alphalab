from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

if TYPE_CHECKING:
    from google.genai import Client as GenaiClient
    from openai import AsyncOpenAI


class AppSettings(BaseSettings):
    app_name: str = Field(default="AlphaLab", alias="AL_APP_NAME")
    app_version: str = Field(default="0.0.0", alias="AL_APP_VERSION")
    debug: bool = Field(default=False, alias="AL_DEBUG")
    base_url: str = Field(default="http://localhost:8000", alias="AL_BASE_URL")
    primary_markets: set[str] | None = Field(default=None, alias="AL_PRIMARY_MARKETS")

    @field_validator("primary_markets", mode="before")
    @classmethod
    def parse_primary_markets(cls, v):
        if isinstance(v, str):
            return {m.strip() for m in v.split(",") if m.strip()}
        return []

    model_config = {
        "env_file": "app_settings.env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
    }


class SecuritySettings(BaseSettings):
    secret_key: str = Field(default="change-me-in-production", alias="AL_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="AL_JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60 * 24 * 7, alias="AL_JWT_EXPIRE_MINUTES")
    allowed_emails: list[str] | None = Field(default=None, alias="AL_ALLOWED_EMAILS")

    @field_validator("allowed_emails", mode="before")
    @classmethod
    def parse_allowed_emails(cls, v) -> list[str]:
        if isinstance(v, str):
            return [e.strip().lower() for e in v.split(",") if e.strip()]
        return []

    model_config = {
        "env_file": "sec_settings.env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
    }


class ExternalIdentitySettings(BaseSettings):
    github_client_id: str = Field(default="", alias="AL_GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="AL_GITHUB_CLIENT_SECRET")
    linkedin_client_id: str = Field(default="", alias="AL_LINKEDIN_CLIENT_ID")
    linkedin_client_secret: str = Field(default="", alias="AL_LINKEDIN_CLIENT_SECRET")

    model_config = {
        "env_file": "external_identity_providers.env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
    }


class AIVendorConfig(BaseSettings):
    vendor_name: str = ""
    api_tier: str = ""
    api_key: str | None = Field(default=None)
    endpoint: str | None = Field(default=None)
    models: set[str] | None = Field(default=None)

    @field_validator("models", mode="before")
    @classmethod
    def parse_models(cls, v: str) -> set[str]:
        if isinstance(v, str):
            return set(model.strip() for model in v.split(",") if model.strip())
        return set()


def _normalize_vendor_name(vendor: str) -> str:
    """Normalize vendor name by removing spaces, hyphens, and underscores."""
    return vendor.upper().replace(" ", "").replace("-", "").replace("_", "")


class AIVendorSettings(BaseSettings):
    """AI/LLM vendor configuration loaded from nested env vars.

    Env var format: AL_LLM__{VENDOR}__{TIER}__{FIELD}

    Example:
        AL_LLM__GEMINI__LOWCOST__MODELS="gemini-3.5-flash-lite, gemini-3.6-flash"
        AL_LLM__GEMINI__LOWCOST__API_KEY="your-api-key"
        AL_LLM__GEMINI__LOWCOST__ENDPOINT="https://generativelanguage.googleapis.com"
        AL_LLM__OPENAI__PREMIUM__MODELS="gpt-5.6-terra, gpt-5.6-sol"
        AL_LLM__OPENAI__PREMIUM__API_KEY="sk-..."
        AL_LLM__OPENAI__PREMIUM__ENDPOINT="https://api.openai.com"
    """

    vendors: dict[str, dict[str, AIVendorConfig]] = Field(alias="AL_LLM", default={})

    model_config = {
        "env_file": "ai_vendors.env",
        "env_file_encoding": "utf-8",
        "env_nested_delimiter": "__",
        "nested_model_default_partial_update": True,
        "populate_by_name": True,
        "extra": "ignore",
    }

    def _find_vendor_tiers(self, vendor: str) -> dict[str, AIVendorConfig] | None:
        """Find vendor tiers by trying normalized matching against configured vendors."""
        normalized = _normalize_vendor_name(vendor)
        for key, tiers in self.vendors.items():
            if _normalize_vendor_name(key) == normalized:
                return tiers
        return None

    def get_ai_client(self, vendor: str, tier: str, timeout_sec: float = 180) -> GenaiClient | AsyncOpenAI | None:
        """Return an async AI client for the given vendor and tier, or None if not found.

        Supported vendors:
        - gemini → google.genai.Client (async)
        - openai → openai.AsyncOpenAI
        - azure_openai / azureopenai → openai.AsyncOpenAI (with DefaultAzureCredential)
        - openrouter → openai.AsyncOpenAI
        """
        from google import genai
        from google.genai.types import HttpOptions
        from openai import AsyncOpenAI

        timeout_sec = timeout_sec if timeout_sec > 0 else 30

        normalized_vendor = _normalize_vendor_name(vendor)
        tier_upper = tier.upper()

        vendor_tiers = self._find_vendor_tiers(vendor)
        if not vendor_tiers:
            return None

        cfg = vendor_tiers.get(tier_upper)
        if not cfg:
            return None

        if normalized_vendor == "GEMINI":
            client = genai.Client(api_key=cfg.api_key, http_options=HttpOptions(timeout=int(timeout_sec * 1000)))
            return client

        if normalized_vendor == "OPENROUTER":
            client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.endpoint, project="AlphaLab", timeout=timeout_sec)
            return client

        if normalized_vendor == "OPENAI":
            kwargs: dict = {"api_key": cfg.api_key, "project": "AlphaLab"}
            if cfg.endpoint:
                kwargs["base_url"] = cfg.endpoint
            client = AsyncOpenAI(**kwargs)
            return client

        if normalized_vendor == "AZUREOPENAI":
            from azure.identity import EnvironmentCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(EnvironmentCredential(), "https://ai.azure.com/.default")
            kwargs = {
                "api_key": token_provider(),
                "base_url": cfg.endpoint,
                "project": "AlphaLab",
                "timeout": timeout_sec,
            }
            client = AsyncOpenAI(**kwargs)
            return client

        return None


class AITaskConfig(BaseSettings):
    task_id: str = ""
    vendor: str = ""
    tier: str = ""
    model: str = ""
    temperature: float | None = None
    web_search: bool = False
    reasoning_level: Literal["low", "medium", "high"] | None = None

    @field_validator("reasoning_level", mode="before")
    @classmethod
    def normalize_reasoning_level(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized or None
        return value


class AITaskSettings(BaseSettings):
    """AI task configuration loaded from nested env vars.

    Env var format: AL_TASK__{TASK_NAME}__{FIELD}

    Example:
        AL_TASK__ANALYZE_TICKER__VENDOR=Gemini
        AL_TASK__ANALYZE_TICKER__TIER=Premium
        AL_TASK__ANALYZE_TICKER__MODEL=gemini-3.1-pro-preview
        AL_TASK__ANALYZE_TICKER__WEB_SEARCH=true
        AL_TASK__ANALYZE_TICKER__REASONING_LEVEL=Medium
    """

    tasks: dict[str, AITaskConfig] = Field(alias="AL_TASK", default={})

    model_config = {
        "env_file": "ai_tasks.env",
        "env_file_encoding": "utf-8",
        "env_nested_delimiter": "__",
        "nested_model_default_partial_update": True,
        "populate_by_name": True,
        "extra": "ignore",
    }

    def get_ai_client(self, task_id: str) -> GenaiClient | AsyncOpenAI | None:
        """Return an AI client for the given task, or None if not configured."""
        task_config = self.tasks.get(task_id.upper())
        if not task_config or not task_config.vendor or not task_config.tier:
            return None
        return ai_vendor_settings.get_ai_client(task_config.vendor, task_config.tier)


class DataStoreSettings(BaseSettings):
    """Redis data store configuration.

    Env vars:
        AL_DATASTORE_REDIS_URL - Redis connection URL
            Non-SSL: redis://[:password@]host:port/db
            SSL/TLS: rediss://[:password@]host:port/db  (note the double 's')
        AL_DATASTORE_REDIS_KEY_PREFIX - Key namespace prefix (default: al:)
    """

    redis_url: str = Field(default="redis://localhost:6379/0", alias="AL_DATASTORE_REDIS_URL")
    key_prefix: str = Field(default="al:", alias="AL_DATASTORE_REDIS_KEY_PREFIX")
    redis_enabled: bool = False
    redis_client: object | None = None

    model_config = {
        "env_file": "datastore.env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
        "arbitrary_types_allowed": True,
    }


app_settings = AppSettings()
security_settings = SecuritySettings()
identity_settings = ExternalIdentitySettings()
ai_vendor_settings = AIVendorSettings()
ai_task_settings = AITaskSettings()
datastore_settings = DataStoreSettings()
