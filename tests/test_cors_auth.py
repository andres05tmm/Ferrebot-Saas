"""CORS QUIRÚRGICO de las rutas públicas de auth (plan Melquiadez §3, M4).

Matriz que se verifica aquí (origin × ruta → ¿CORS?):

    origin                 | /auth/login/password | /auth/reset/solicitar | /api/v1/ventas (negocio)
    -----------------------|----------------------|-----------------------|-------------------------
    https://melquiadez.com | ✅ permitido         | ✅ permitido          | ❌ sin headers CORS
    https://evil.com       | ❌ 400, sin allow    | ❌ 400, sin allow     | ❌ sin headers CORS
    http://localhost:5173  | solo si está en env  | solo si está en env   | ❌ sin headers CORS

El preflight OPTIONS no toca DB (lo responde el CORSMiddleware antes del TenantMiddleware), así que
estas pruebas corren sin Postgres/Redis.
"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from apps.api.main import create_app
from core.config import get_settings

LANDING = "https://melquiadez.com"
LOGIN_PATH = "/api/v1/auth/login/password"
RESET_PATH = "/api/v1/auth/reset/solicitar"
NEGOCIO_PATH = "/api/v1/ventas"


async def _preflight(path: str, origin: str, app=None) -> httpx.Response:
    """Dispara un preflight CORS (OPTIONS con los headers que manda el browser)."""
    app = app or create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
    ) as c:
        return await c.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )


async def test_preflight_login_desde_landing_permitido():
    r = await _preflight(LOGIN_PATH, LANDING)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == LANDING
    # El método que pide la landing (POST) está explícitamente permitido.
    assert "POST" in r.headers.get("access-control-allow-methods", "")


async def test_preflight_reset_desde_landing_permitido():
    r = await _preflight(RESET_PATH, LANDING)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == LANDING


async def test_preflight_login_desde_otro_origin_rechazado():
    r = await _preflight(LOGIN_PATH, "https://evil.com")
    # Starlette responde 400 al preflight de un origin no permitido y NO emite allow-origin.
    assert r.headers.get("access-control-allow-origin") is None
    assert r.status_code == 400


async def test_endpoint_de_negocio_no_devuelve_cors():
    # Aunque el origin sea la landing, una ruta de negocio JAMÁS recibe headers CORS (no está en scope).
    r = await _preflight(NEGOCIO_PATH, LANDING)
    assert r.headers.get("access-control-allow-origin") is None
    assert "access-control-allow-methods" not in r.headers


async def test_localhost_solo_si_esta_en_settings(monkeypatch: pytest.MonkeyPatch):
    """El origin de Vite (dev) se permite SOLO cuando está en `cors_allow_origins` (env), nunca por
    defecto. Demuestra que los origins son configurables y que prod (sin localhost) lo rechaza."""
    dev_origin = "http://localhost:5173"
    # Por defecto (sin localhost en env) → rechazado.
    r_default = await _preflight(LOGIN_PATH, dev_origin)
    assert r_default.headers.get("access-control-allow-origin") is None

    # Con localhost agregado por env → permitido (app reconstruida con settings frescas).
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", f"{LANDING},{dev_origin}")
    get_settings.cache_clear()
    try:
        app = create_app()
        r = await _preflight(LOGIN_PATH, dev_origin, app=app)
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == dev_origin
    finally:
        get_settings.cache_clear()   # no filtrar la settings override a otras pruebas
