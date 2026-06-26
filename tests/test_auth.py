from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.routers import auth as auth_router


def test_login_page_renders(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "Welcome back" in response.text


def test_login_page_no_providers_when_unconfigured(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "No login providers configured" in response.text


def test_oauth_login_unknown_provider(client: TestClient) -> None:
    response = client.get("/auth/unknown/login", follow_redirects=False)
    assert response.status_code == 404


def test_oauth_login_requests_reauthentication_prompt(client: TestClient, monkeypatch: object) -> None:
    class FakeOAuthClient:
        async def get_authorization_url(
            self,
            redirect_uri: str,
            state: str | None = None,
            scope: list[str] | None = None,
            code_challenge: str | None = None,
            code_challenge_method: str | None = None,
            extras_params: dict | None = None,
        ) -> str:
            assert extras_params == {"prompt": "login", "allow_signup": "false", "login": ""}
            return "https://example.com/oauth"

    monkeypatch.setattr(
        auth_router.oauth,
        "OAUTH_PROVIDERS",
        {"github": {"client": FakeOAuthClient(), "scopes": ["user:email"]}},
    )

    response = client.get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/oauth"


def test_oauth_callback_shows_reason_when_provider_returns_error(client: TestClient, monkeypatch: object) -> None:
    monkeypatch.setattr(
        auth_router.oauth,
        "OAUTH_PROVIDERS",
        {"linkedin": {"client": object(), "scopes": []}},
    )

    response = client.get(
        "/auth/linkedin/callback?error=user_cancelled_authorize&error_description=The+user+cancelled+the+authorization",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Login failed" in response.text
    assert "The user cancelled the authorization" in response.text


def test_oauth_callback_shows_reason_when_code_is_invalid(client: TestClient, monkeypatch: object) -> None:
    class FakeOAuthClient:
        async def get_access_token(self, code: str, callback_url: str) -> dict[str, str]:
            raise RuntimeError("invalid_grant: authorization code has expired")

    monkeypatch.setattr(
        auth_router.oauth,
        "OAUTH_PROVIDERS",
        {"github": {"client": FakeOAuthClient(), "scopes": []}},
    )

    response = client.get("/auth/github/callback?code=expired-code", follow_redirects=False)

    assert response.status_code == 400
    assert "Login failed" in response.text
    assert "invalid_grant" in response.text


def test_oauth_callback_handles_profile_lookup_failure(client: TestClient, monkeypatch: object) -> None:
    class FakeHttpxClient:
        async def __aenter__(self) -> "FakeHttpxClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict | None = None) -> None:
            raise RuntimeError("provider profile lookup failed")

    class FakeOAuthClient:
        async def get_access_token(self, code: str, callback_url: str) -> dict[str, str]:
            return {"access_token": "token"}

        def get_httpx_client(self) -> FakeHttpxClient:
            return FakeHttpxClient()

    monkeypatch.setattr(
        auth_router.oauth,
        "OAUTH_PROVIDERS",
        {"github": {"client": FakeOAuthClient(), "scopes": []}},
    )

    response = client.get("/auth/github/callback?code=bad-profile", follow_redirects=False)

    assert response.status_code == 400
    assert "Login failed" in response.text
    assert "provider profile lookup failed" in response.text


def test_oauth_callback_shows_returned_email_when_access_denied(client: TestClient, monkeypatch: object) -> None:
    class FakeHttpxClient:
        async def __aenter__(self) -> "FakeHttpxClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict | None = None) -> SimpleNamespace:
            return SimpleNamespace(
                json=lambda: {
                    "id": 123,
                    "name": "Test User",
                    "login": "test-user",
                    "email": "blocked@example.com",
                    "avatar_url": "https://example.com/avatar.png",
                }
            )

    class FakeOAuthClient:
        async def get_access_token(self, code: str, callback_url: str) -> dict[str, str]:
            return {"access_token": "token"}

        def get_httpx_client(self) -> FakeHttpxClient:
            return FakeHttpxClient()

    monkeypatch.setattr(
        auth_router.oauth,
        "OAUTH_PROVIDERS",
        {"github": {"client": FakeOAuthClient(), "scopes": []}},
    )
    monkeypatch.setattr(auth_router.config.security_settings, "allowed_emails", ["allowed@example.com"])

    response = client.get("/auth/github/callback?code=test-code", follow_redirects=False)

    assert response.status_code == 403
    assert "blocked@example.com" in response.text
    assert "not authorized" in response.text.lower()


def test_logout_clears_cookie(client: TestClient) -> None:
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "access_token" in response.headers.get("set-cookie", "")
