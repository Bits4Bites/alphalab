from datetime import UTC, datetime, timedelta

import jwt

from app.config import security_settings


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=security_settings.jwt_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, security_settings.secret_key, algorithm=security_settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, security_settings.secret_key, algorithms=[security_settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
