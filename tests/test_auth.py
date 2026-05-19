from fastapi.testclient import TestClient


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


def test_logout_clears_cookie(client: TestClient) -> None:
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "access_token" in response.headers.get("set-cookie", "")
