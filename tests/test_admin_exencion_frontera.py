"""Endurecimiento de la exención de tenant del panel /admin (ADR 0010 §D2).

La exención del TenantMiddleware debe matchear /admin SOLO en frontera de segmento (el prefijo exacto o
seguido de '/'). Una ruta futura como /api/v1/admin-reports NO debe heredar el bypass por accidente:
sigue siendo una ruta /api por-empresa y, sin tenant resuelto, el middleware la rechaza.
"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from apps.api.main import create_app
from core.auth import create_platform_token
from core.tenancy.middleware import _ADMIN_PREFIX, _es_ruta_plataforma


def test_predicado_frontera_de_segmento():
    assert _es_ruta_plataforma(_ADMIN_PREFIX) is True                 # prefijo exacto
    assert _es_ruta_plataforma(_ADMIN_PREFIX + "/tenants") is True    # sub-ruta
    assert _es_ruta_plataforma(_ADMIN_PREFIX + "/") is True
    # NO exentas: comparten el texto del prefijo pero no la frontera de segmento.
    assert _es_ruta_plataforma("/api/v1/admin-reports") is False
    assert _es_ruta_plataforma("/api/v1/adminX") is False
    assert _es_ruta_plataforma("/api/v1/ventas") is False


async def _get(path: str, token: str | None = None) -> httpx.Response:
    app = create_app()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
    ) as c:
        return await c.get(path, headers=headers)


async def test_adminX_no_queda_exenta_resuelve_tenant():
    # /api/v1/adminX NO es del panel: pasa por el TenantMiddleware. Sin slug (localhost, token de
    # plataforma sin claim tenant) → 404 'Empresa no encontrada' (la resolución de tenant SÍ corrió).
    r = await _get("/api/v1/adminX", create_platform_token(user_id=0, rol="super_admin"))
    assert r.status_code == 404
    assert r.json()["detail"] == "Empresa no encontrada"


async def test_admin_panel_si_exento_llega_al_gate():
    # Contraste: /api/v1/admin/tenants SÍ está exento → no resuelve tenant; llega al gate de auth, que
    # sin token responde 401 'Falta el token' (NO el 404 de resolución de empresa).
    r = await _get("/api/v1/admin/tenants", None)
    assert r.status_code == 401
    assert r.json()["detail"] == "Falta el token"
