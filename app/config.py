from pydantic import Field
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


app_settings = AppSettings()
security_settings = SecuritySettings()
identity_settings = ExternalIdentitySettings()
