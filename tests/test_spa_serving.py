"""E3 — servir el SPA del dashboard desde FastAPI (build de Vite con fallback a index.html).

create_app(spa_dist=...) monta, DESPUÉS de los routers /api/v1, una ruta catch-all que devuelve
index.html para rutas de cliente (las que NO empiezan por /api/), sirviendo los assets reales del
build cuando existen. Debe ser resiliente si `dist` no existe (no rompe el arranque; catch-all 404).
"""
import httpx
import pytest
from httpx import ASGITransport

from apps.api.main import DASHBOARD_DIST, create_app

_SENTINEL = '<div id="root">SPA OK</div>'


def _make_dist(tmp_path):
    """Crea un `dist` mínimo tipo Vite (index.html + assets/) y lo devuelve."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(f"<!doctype html>{_SENTINEL}", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return dist


def _cliente(app) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_ruta_api_no_es_interceptada_por_el_fallback(tmp_path):
    """Una ruta /api/v1/... pasa por el stack del API (middleware/router), NO por el SPA."""
    app = create_app(spa_dist=_make_dist(tmp_path))
    async with _cliente(app) as c:
        r = await c.get("/api/v1/config")
    assert _SENTINEL not in r.text          # no devolvió el index del SPA
    assert r.status_code != 200             # sin tenant ni token no resuelve


async def test_ruta_de_cliente_devuelve_index(tmp_path):
    """Una ruta de cliente (history API del SPA) cae al index.html cuando `dist` existe."""
    app = create_app(spa_dist=_make_dist(tmp_path))
    async with _cliente(app) as c:
        r = await c.get("/historial")
    assert r.status_code == 200, r.text
    assert _SENTINEL in r.text


async def test_dist_ausente_no_rompe_el_app(tmp_path):
    """Sin build (`dist` inexistente) el app arranca: /health responde y el catch-all da 404."""
    app = create_app(spa_dist=tmp_path / "no_existe")
    async with _cliente(app) as c:
        salud = await c.get("/health")
        cliente = await c.get("/historial")
    assert salud.status_code == 200
    assert cliente.status_code == 404


async def test_build_real_servido_por_la_api():
    """Con el dist REAL (tras `npm run build`): GET / → index.html, un asset resuelve, /api no se intercepta.

    Skip si no hay build (mismo criterio que el catch-all): el dist no se versiona.
    """
    if not (DASHBOARD_DIST / "index.html").is_file():
        pytest.skip("dashboard/dist no existe (correr `npm run build` en dashboard/)")

    app = create_app()  # usa DASHBOARD_DIST por defecto
    assets = sorted((DASHBOARD_DIST / "assets").iterdir()) if (DASHBOARD_DIST / "assets").is_dir() else []
    async with _cliente(app) as c:
        raiz = await c.get("/")
        api = await c.get("/api/v1/config")
        asset = await c.get(f"/assets/{assets[0].name}") if assets else None

    assert raiz.status_code == 200
    assert '<div id="root">' in raiz.text          # sirvió el index.html del build
    assert "SPA OK" not in api.text                 # /api lo maneja el stack, no el SPA
    assert api.status_code != 200
    if asset is not None:
        assert asset.status_code == 200             # un asset real del build resuelve
