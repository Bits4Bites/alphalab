from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import app_settings, security_settings
from app.services.auth import create_access_token
from app.services.oauth import OAUTH_PROVIDERS, get_enabled_providers

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    providers = get_enabled_providers()
    return templates.TemplateResponse(request, "login.html", {"providers": providers})


@router.get("/auth/{provider}/login")
async def oauth_login(provider: str) -> RedirectResponse:
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    oauth = OAUTH_PROVIDERS[provider]
    callback_url = f"{app_settings.base_url}/auth/{provider}/callback"
    authorization_url = await oauth["client"].get_authorization_url(callback_url, scope=oauth["scopes"])
    return RedirectResponse(url=authorization_url)


@router.get("/auth/{provider}/callback")
async def oauth_callback(provider: str, code: str, response: Response) -> RedirectResponse:
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    oauth = OAUTH_PROVIDERS[provider]
    callback_url = f"{app_settings.base_url}/auth/{provider}/callback"

    try:
        access_token = await oauth["client"].get_access_token(code, callback_url)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to obtain access token")

    token_value = access_token["access_token"]

    # Fetch user info from provider
    async with oauth["client"].get_httpx_client() as client:
        if provider == "github":
            resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token_value}"},
            )
            user_data = resp.json()
            user_info = {
                "sub": str(user_data["id"]),
                "name": user_data.get("name") or user_data["login"],
                "email": user_data.get("email", ""),
                "avatar": user_data.get("avatar_url", ""),
                "provider": "github",
            }
        elif provider == "linkedin":
            resp = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {token_value}"},
            )
            user_data = resp.json()
            user_info = {
                "sub": user_data.get("sub", ""),
                "name": user_data.get("name", ""),
                "email": user_data.get("email", ""),
                "avatar": user_data.get("picture", ""),
                "provider": "linkedin",
            }
        else:
            raise HTTPException(status_code=400, detail="Unsupported provider")

    # Create JWT and set as cookie
    jwt_token = create_access_token(user_info)
    redirect = RedirectResponse(url="/dashboard", status_code=302)
    redirect.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=not app_settings.debug,
        samesite="lax",
        max_age=security_settings.jwt_expire_minutes * 60,
    )
    return redirect


@router.get("/logout")
async def logout() -> RedirectResponse:
    redirect = RedirectResponse(url="/login", status_code=302)
    redirect.delete_cookie(key="access_token")
    return redirect
