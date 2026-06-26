from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_403_FORBIDDEN

from app import config, templating
from app.services import auth as auth_service
from app.services import oauth

router = APIRouter(tags=["auth"])


def _render_login_error(
    request: Request,
    *,
    title: str,
    message: str,
    detail: str | None = None,
    user_email: str | None = None,
    allowed_emails: list[str] | None = None,
    status_code: int = HTTP_400_BAD_REQUEST,
) -> HTMLResponse:
    context = {
        "app_name": config.app_settings.app_name,
        "title": title,
        "message": message,
        "detail": detail,
        "user_email": user_email,
        "allowed_emails": allowed_emails or [],
    }
    response = templating.templates.TemplateResponse(request, "login_error.html", context)
    response.status_code = status_code
    return response


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
    authorization_url = await oauth_cfg["client"].get_authorization_url(
        callback_url,
        scope=oauth_cfg["scopes"],
        extras_params=oauth.AUTHORIZATION_EXTRAS_PARAMS.get(provider),
    )
    return RedirectResponse(url=authorization_url)


@router.get("/auth/{provider}/callback", response_model=None)
async def oauth_callback(
    request: Request,
    provider: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    response: Response = None,
) -> RedirectResponse | HTMLResponse:
    if provider not in oauth.OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not supported")

    if error:
        failure_reason = error_description or error
        return _render_login_error(
            request,
            title="Login failed",
            message="The sign-in was cancelled or rejected by the identity provider.",
            detail=f"Reason: {failure_reason}",
            status_code=HTTP_400_BAD_REQUEST,
        )

    oauth_cfg = oauth.OAUTH_PROVIDERS[provider]
    callback_url = f"{config.app_settings.base_url}/auth/{provider}/callback"

    try:
        access_token = await oauth_cfg["client"].get_access_token(code, callback_url)
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
        allowed_emails = config.security_settings.allowed_emails or []
        if not user_email or user_email not in allowed_emails:
            return _render_login_error(
                request,
                title="Access denied",
                message="Your email address is not authorized to access this application.",
                detail=(
                    "Please sign in with an approved account or contact the administrator "
                    "if you believe this is an error."
                ),
                user_email=user_email or None,
                allowed_emails=allowed_emails,
                status_code=HTTP_403_FORBIDDEN,
            )

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
    except Exception as exc:
        failure_reason = str(exc).strip() or "The identity provider did not provide a specific error message."
        return _render_login_error(
            request,
            title="Login failed",
            message="We could not complete your sign-in because the identity provider rejected the request.",
            detail=f"Reason: {failure_reason}",
            status_code=HTTP_400_BAD_REQUEST,
        )


@router.get("/logout")
async def logout() -> RedirectResponse:
    redirect = RedirectResponse(url="/", status_code=302)
    redirect.delete_cookie(key="access_token")
    return redirect
