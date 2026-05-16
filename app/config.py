from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

if TYPE_CHECKING:
    from google.genai import Client as GenaiClient
    from openai import AsyncOpenAI


class AppSettings(BaseSettings):
    app_name: str = Field(default="AlphaLab", alias="AL_APP_NAME")
    debug: bool = Field(default=False, alias="AL_DEBUG")
    base_url: str = Field(default="http://localhost:8000", alias="AL_BASE_URL")

    model_config = {"env_file": "app_settings.env", "env_file_encoding": "utf-8", "populate_by_name": True}


class SecuritySettings(BaseSettings):
    secret_key: str = Field(default="change-me-in-production", alias="AL_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="AL_JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60 * 24 * 7, alias="AL_JWT_EXPIRE_MINUTES")

    model_config = {"env_file": "sec_settings.env", "env_file_encoding": "utf-8", "populate_by_name": True}


class ExternalIdentitySettings(BaseSettings):
    github_client_id: str = Field(default="", alias="AL_GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="AL_GITHUB_CLIENT_SECRET")
    linkedin_client_id: str = Field(default="", alias="AL_LINKEDIN_CLIENT_ID")
    linkedin_client_secret: str = Field(default="", alias="AL_LINKEDIN_CLIENT_SECRET")

    model_config = {
        "env_file": "external_identity_providers.env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
    }


class AIVendorConfig(BaseSettings):
    vendor_name: str = ""
    api_tier: str = ""
    api_key: str | None = Field(default=None)
    endpoint: str | None = Field(default=None)
    models: set[str] | None = Field(default=None)

    @field_validator("models", mode="before")
    @classmethod
    def decode_models(cls, v: str) -> set[str]:
        if isinstance(v, str):
            return set(model.strip() for model in v.split(",") if model.strip())
        return set()


class AIVendorSettings(BaseSettings):
    """AI/LLM vendor configuration loaded from nested env vars.

    Env var format: AL_LLM__{VENDOR}__{TIER}__{FIELD}

    Example:
        AL_LLM__GEMINI__FREE__MODELS="gemini-2.5-flash, gemini-2.5-flash-lite"
        AL_LLM__GEMINI__FREE__API_KEY="your-api-key"
        AL_LLM__GEMINI__FREE__ENDPOINT="https://generativelanguage.googleapis.com"
        AL_LLM__OPENAI__PREMIUM__MODELS="gpt-4o, o1-preview"
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
    }

    def get_ai_client(self, vendor: str, tier: str, timeout_sec: float = 180) -> GenaiClient | AsyncOpenAI | None:
        """Return an async AI client for the given vendor and tier, or None if not found.

        Supported vendors:
        - gemini → google.genai.Client (async)
        - openai → openai.AsyncOpenAI
        - azure_openai / azureopenai → openai.AsyncOpenAI (with DefaultAzureCredential)
        """
        from google import genai
        from google.genai.types import HttpOptions
        from openai import AsyncOpenAI

        timeout_sec = timeout_sec if timeout_sec > 0 else 30

        vendor_upper = vendor.upper()
        tier_upper = tier.upper()

        vendor_tiers = self.vendors.get(vendor_upper)
        if not vendor_tiers:
            return None

        cfg = vendor_tiers.get(tier_upper)
        if not cfg:
            return None

        if vendor_upper == "GEMINI":
            client = genai.Client(api_key=cfg.api_key, http_options=HttpOptions(timeout=int(timeout_sec * 1000)))
            return client

        if vendor_upper in ("OPENROUTER", "OPEN_ROUTER", "OPEN-ROUTER"):
            client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.endpoint, project="AlphaLab", timeout=timeout_sec)
            return client

        if vendor_upper == "OPENAI":
            kwargs: dict = {"api_key": cfg.api_key, "project": "AlphaLab"}
            if cfg.endpoint:
                kwargs["base_url"] = cfg.endpoint
            client = AsyncOpenAI(**kwargs)
            return client

        if vendor_upper in ("AZUREOPENAI", "AZURE_OPENAI", "AZURE-OPENAI"):
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


app_settings = AppSettings()
security_settings = SecuritySettings()
identity_settings = ExternalIdentitySettings()
ai_vendor_settings = AIVendorSettings()
