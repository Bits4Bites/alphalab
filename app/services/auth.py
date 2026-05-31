from datetime import UTC, datetime, timedelta

import jwt

from app import config


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=config.security_settings.jwt_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.security_settings.secret_key, algorithm=config.security_settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token, config.security_settings.secret_key, algorithms=[config.security_settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
