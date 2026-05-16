from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


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

app_settings = AppSettings()
security_settings = SecuritySettings()
identity_settings = ExternalIdentitySettings()
ai_vendor_settings = AIVendorSettings()
