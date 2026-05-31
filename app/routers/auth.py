from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app import config, templating
from app.services import auth as auth_service
from app.services import oauth

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    providers = oauth.get_enabled_providers()
    return templating.templates.TemplateResponse(request, "login.html", {"providers": providers})


@router.get("/auth/{provider}/login")
async def oauth_login(provider: str) -> RedirectResponse:
    if provider not in oauth.OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    oauth_cfg = oauth.OAUTH_PROVIDERS[provider]
    callback_url = f"{config.app_settings.base_url}/auth/{provider}/callback"
    authorization_url = await oauth_cfg["client"].get_authorization_url(callback_url, scope=oauth_cfg["scopes"])
    return RedirectResponse(url=authorization_url)


@router.get("/auth/{provider}/callback")
async def oauth_callback(provider: str, code: str, response: Response) -> RedirectResponse:
    if provider not in oauth.OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    oauth_cfg = oauth.OAUTH_PROVIDERS[provider]
    callback_url = f"{config.app_settings.base_url}/auth/{provider}/callback"

    try:
        access_token = await oauth_cfg["client"].get_access_token(code, callback_url)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to obtain access token")

    token_value = access_token["access_token"]

    # Fetch user info from provider
    async with oauth_cfg["client"].get_httpx_client() as client:
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

    # Check if user's email is in the allowed list
    user_email = (user_info.get("email") or "").lower()
    if not user_email or user_email not in config.security_settings.allowed_emails:
        raise HTTPException(status_code=403, detail="Access denied. Your email is not in the allowed list.")

    # Create JWT and set as cookie
    jwt_token = auth_service.create_access_token(user_info)
    redirect = RedirectResponse(url="/dashboard", status_code=302)
    redirect.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=not config.app_settings.debug,
        samesite="lax",
        max_age=config.security_settings.jwt_expire_minutes * 60,
    )
    return redirect


@router.get("/logout")
async def logout() -> RedirectResponse:
    redirect = RedirectResponse(url="/", status_code=302)
    redirect.delete_cookie(key="access_token")
    return redirect
