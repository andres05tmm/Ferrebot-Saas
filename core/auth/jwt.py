"""JWT de la API (core/auth). Firma con SECRET_KEY.

Dos formas de token (ADR 0010 §D2):
- **De tenant** (`create_access_token`): claim `tenant` (slug) + `sub` (user_id). Ata a UNA empresa.
- **De plataforma** (`create_platform_token`): para el super-admin (operador SaaS), que opera cross-tenant.
  Lleva `scope=platform` y NUNCA un claim `tenant`. INVARIANTE: sin `tenant`, el resolver no resuelve
  ninguna empresa, así que un token de plataforma jamás opera sobre la base de un tenant por error.
"""
from datetime import timedelta

from jose import JWTError, jwt

from core.config import get_settings
from core.config.timezone import now_co


def _exp_iat(settings) -> dict:
    now = now_co()
    return {
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }


def create_access_token(*, user_id: int, tenant: str, rol: str) -> str:
    settings = get_settings()
    payload = {"sub": str(user_id), "tenant": tenant, "rol": rol, "scope": "tenant", **_exp_iat(settings)}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_platform_token(*, user_id: int, rol: str = "super_admin") -> str:
    """JWT de plataforma (super-admin): SIN claim `tenant`, CON `scope=platform`."""
    settings = get_settings()
    payload = {"sub": str(user_id), "rol": rol, "scope": "platform", **_exp_iat(settings)}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def decode_token_optional(token: str) -> dict | None:
    try:
        return decode_token(token)
    except JWTError:
        return None
