"""JWT de la API (core/auth). Firma con SECRET_KEY; claim `tenant` (slug) + `sub` (user_id)."""
from datetime import timedelta

from jose import JWTError, jwt

from core.config import get_settings
from core.config.timezone import now_co


def create_access_token(*, user_id: int, tenant: str, rol: str) -> str:
    settings = get_settings()
    now = now_co()
    payload = {
        "sub": str(user_id),
        "tenant": tenant,
        "rol": rol,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def decode_token_optional(token: str) -> dict | None:
    try:
        return decode_token(token)
    except JWTError:
        return None
