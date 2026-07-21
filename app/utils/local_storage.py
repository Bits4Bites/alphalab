import base64
import hashlib
import hmac

from app import config

_KEY_SIZE_BYTES = 16
_HMAC_CONTEXT = "alphalab-local-storage:v1"


def _normalize_claim(value: object) -> str:
    return str(value).strip() if value is not None else ""


def derive_user_key(user: dict[str, object]) -> str:
    """Derive a stable pseudonymous browser-storage key for an authenticated user."""
    provider = _normalize_claim(user.get("provider")).casefold()
    subject = _normalize_claim(user.get("sub"))
    email = _normalize_claim(user.get("email")).casefold()

    if provider and subject:
        identity = f"oauth:{provider}:{subject}"
    elif email:
        identity = f"email:{email}"
    else:
        raise ValueError("Authenticated user has no stable identity claim.")

    secret = config.security_settings.secret_key
    if not secret:
        raise ValueError("AL_SECRET_KEY must be configured to derive a local-storage user key.")

    message = f"{_HMAC_CONTEXT}:{identity}".encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()[:_KEY_SIZE_BYTES]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
