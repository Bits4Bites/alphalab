from httpx_oauth.clients.github import GitHubOAuth2
from httpx_oauth.clients.linkedin import LinkedInOAuth2

from app.config import identity_settings

github_oauth_client = GitHubOAuth2(
    client_id=identity_settings.github_client_id,
    client_secret=identity_settings.github_client_secret,
)

linkedin_oauth_client = LinkedInOAuth2(
    client_id=identity_settings.linkedin_client_id,
    client_secret=identity_settings.linkedin_client_secret,
)

OAUTH_PROVIDERS: dict[str, dict] = {
    "github": {
        "client": github_oauth_client,
        "scopes": ["user:email"],
        "enabled": bool(identity_settings.github_client_id and identity_settings.github_client_secret),
        "label": "Continue with GitHub",
        "icon": "github",
    },
    "linkedin": {
        "client": linkedin_oauth_client,
        "scopes": ["openid", "profile", "email"],
        "enabled": bool(identity_settings.linkedin_client_id and identity_settings.linkedin_client_secret),
        "label": "Continue with LinkedIn",
        "icon": "linkedin",
    },
}


def get_enabled_providers() -> list[dict]:
    return [
        {"name": name, "label": provider["label"], "icon": provider["icon"]}
        for name, provider in OAUTH_PROVIDERS.items()
        if provider["enabled"]
    ]
