from fastapi.testclient import TestClient

from app.services.auth import create_access_token


def test_dashboard_redirects_when_unauthenticated(client: TestClient) -> None:
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_dashboard_renders_when_authenticated(client: TestClient) -> None:
    user_data = {
        "sub": "123",
        "name": "Test User",
        "email": "test@example.com",
        "avatar": "",
        "provider": "github",
    }
    token = create_access_token(user_data)
    client.cookies.set("access_token", token)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Test User" in response.text
    assert "Analyze Ticker" in response.text
    assert "Build Portfolio" in response.text
    assert "Review Portfolio" in response.text
