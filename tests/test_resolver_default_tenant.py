"""Fallback de empresa por defecto en `resolve_slug` (Fase 14, pre-deploy single-tenant).

`DEFAULT_TENANT_SLUG` es el ÚLTIMO recurso: solo aplica cuando NADA explícito (subdominio, header
`X-Tenant-Slug`, claim `tenant` del JWT) resolvió. Sin el env → comportamiento idéntico al de hoy
(`None`), así que el aislamiento multi-tenant queda intacto. Estas pruebas fijan la precedencia y el
opt-in, más un sanity de que un JWT de la empresa por defecto coincide con el tenant resuelto (no 403).
"""
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import HTTPConnection, Request

from core.auth.deps import get_current_user
from core.auth.jwt import create_access_token
from core.tenancy.resolver import resolve_slug


def _scope(*, host: str = "testserver", headers: dict[str, str] | None = None) -> dict:
    raw = [(b"host", host.encode())]
    for clave, valor in (headers or {}).items():
        raw.append((clave.lower().encode(), valor.encode()))
    return {"type": "http", "headers": raw, "state": {}}


def _conn(*, host: str = "testserver", headers: dict[str, str] | None = None) -> HTTPConnection:
    return HTTPConnection(_scope(host=host, headers=headers))


@pytest.fixture
def settings_env(monkeypatch):
    """Setea env de plataforma y limpia el caché de settings (antes y después del test)."""
    from core.config import get_settings

    def _set(**env: str | None) -> None:
        for clave, valor in env.items():
            if valor is None:
                monkeypatch.delenv(clave, raising=False)
            else:
                monkeypatch.setenv(clave, valor)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


# Host estilo Railway (sin subdominio del BASE_DOMAIN): no resuelve por subdominio.
_HOST_RAILWAY = "ferrebot-api-production.up.railway.app"


def test_sin_default_sin_senales_es_none(settings_env):
    """Sin DEFAULT_TENANT_SLUG y sin señales → None (comportamiento de siempre)."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="ferrebot.app")
    assert resolve_slug(_conn(host=_HOST_RAILWAY)) is None


def test_con_default_sin_senales_devuelve_default(settings_env):
    """Con DEFAULT_TENANT_SLUG y sin subdominio/header/JWT → devuelve el default."""
    settings_env(DEFAULT_TENANT_SLUG="puntorojo", BASE_DOMAIN="ferrebot.app")
    assert resolve_slug(_conn(host=_HOST_RAILWAY)) == "puntorojo"


def test_subdominio_explicito_gana_sobre_default(settings_env):
    """Un subdominio real SIEMPRE gana sobre el default."""
    settings_env(DEFAULT_TENANT_SLUG="puntorojo", BASE_DOMAIN="ferrebot.app")
    assert resolve_slug(_conn(host="otra.ferrebot.app")) == "otra"


def test_header_explicito_gana_sobre_default(settings_env):
    """El header X-Tenant-Slug SIEMPRE gana sobre el default."""
    settings_env(DEFAULT_TENANT_SLUG="puntorojo", BASE_DOMAIN="ferrebot.app")
    conn = _conn(host=_HOST_RAILWAY, headers={"x-tenant-slug": "Otra"})
    assert resolve_slug(conn) == "otra"   # normalizado (strip+lower), como hoy


def test_jwt_explicito_gana_sobre_default(settings_env):
    """El claim `tenant` del JWT SIEMPRE gana sobre el default."""
    settings_env(DEFAULT_TENANT_SLUG="puntorojo", BASE_DOMAIN="ferrebot.app")
    token = create_access_token(user_id=1, tenant="otra", rol="admin")
    conn = _conn(host=_HOST_RAILWAY, headers={"authorization": f"Bearer {token}"})
    assert resolve_slug(conn) == "otra"


def test_default_normaliza_mayusculas_y_espacios(settings_env):
    """El default se normaliza (strip+lower) para casar con los slugs almacenados."""
    settings_env(DEFAULT_TENANT_SLUG="  PuntoRojo  ", BASE_DOMAIN="ferrebot.app")
    assert resolve_slug(_conn(host=_HOST_RAILWAY)) == "puntorojo"


def test_sanity_get_current_user_coincide_con_el_default(settings_env):
    """JWT de `puntorojo` + tenant resuelto por el default `puntorojo` → coincide (no 403)."""
    settings_env(DEFAULT_TENANT_SLUG="puntorojo", BASE_DOMAIN="ferrebot.app")
    # El resolver, sin señales explícitas, entrega el default.
    assert resolve_slug(_conn(host=_HOST_RAILWAY)) == "puntorojo"

    token = create_access_token(user_id=7, tenant="puntorojo", rol="admin")
    request = Request(_scope(host=_HOST_RAILWAY, headers={"authorization": f"Bearer {token}"}))
    request.state.tenant = SimpleNamespace(slug="puntorojo")   # lo que el middleware resolvería del default
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    principal = get_current_user(request, creds)   # claims.tenant == tenant.slug → no 403
    assert principal.tenant == "puntorojo" and principal.user_id == 7
