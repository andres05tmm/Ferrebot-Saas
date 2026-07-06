"""GET /api/v1/manifest.webmanifest — manifest PWA por-tenant (PÚBLICO, sin auth).

Mismo patrón que test_config_router: app mínima + ASGITransport + dependency_overrides. El branding
opcional se inyecta; no se toca el control DB. Verifica: sin auth, marca por-tenant, defaults neutros
sin tenant, content-type de manifest, e íconos maskable presentes.
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from modules.config.router import Branding, get_branding_opcional, router


def _app(branding: Branding | None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _brand() -> Branding | None:
        return branding

    app.dependency_overrides[get_branding_opcional] = _brand
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_manifest_usa_marca_del_tenant():
    branding = Branding(color_primario="#C8200E", nombre_comercial="Punto Rojo")
    app = _app(branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/manifest.webmanifest")
    assert r.status_code == 200, r.text
    # Content-type de manifest (el navegador lo exige para instalar).
    assert r.headers["content-type"].startswith("application/manifest+json")
    body = r.json()
    assert body["name"] == "Punto Rojo"
    assert body["short_name"] == "Punto Rojo"[:12]
    assert body["theme_color"] == "#C8200E"          # instala con el rojo de Punto Rojo
    assert body["display"] == "standalone"
    assert body["start_url"] == "/"
    # Ícono maskable presente (requisito de instalabilidad en Android/Chrome).
    assert any(i.get("purpose") == "maskable" for i in body["icons"])


async def test_manifest_sin_tenant_cae_a_defaults_neutros():
    # Host sin empresa resuelta (link compartido): manifest neutro, NO 404 → la instalación no se rompe.
    app = _app(None)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/manifest.webmanifest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "FerreBot"
    assert body["theme_color"] == "#C8200E"


async def test_manifest_no_exige_auth():
    # Público: sin token no debe devolver 401/403 (el navegador lo pide antes del login).
    app = _app(Branding(color_primario="#000000", nombre_comercial="X"))
    async with _cliente(app) as c:
        r = await c.get("/api/v1/manifest.webmanifest")  # sin Authorization
    assert r.status_code == 200, r.text


async def test_icono_svg_usa_el_color_del_tenant():
    branding = Branding(color_primario="#C8200E", nombre_comercial="Punto Rojo")
    app = _app(branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/icon.svg")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "#C8200E" in r.text                                  # fondo teñido con el color de la empresa
    assert r.text.lstrip().startswith("<svg")


async def test_icono_svg_es_el_icono_principal_del_manifest():
    branding = Branding(color_primario="#000000", nombre_comercial="X")
    app = _app(branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/manifest.webmanifest")
    icons = r.json()["icons"]
    assert icons[0]["src"] == "/api/v1/icon.svg"               # el dinámico va primero (ícono principal)
    assert icons[0]["type"] == "image/svg+xml"
    # Fallbacks estáticos presentes → instalable donde no se honre el SVG.
    assert any(i.get("purpose") == "maskable" for i in icons)


async def test_manifest_tolera_sin_tenant_en_middleware_real():
    # End-to-end con el TenantMiddleware real (create_app): en localhost SIN slug, otras rutas /api
    # darían 404 'Empresa no encontrada'; el manifest está en _TENANT_OPCIONAL → pasa y da 200 neutro.
    from apps.api.main import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
    ) as c:
        r = await c.get("/api/v1/manifest.webmanifest")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "FerreBot"
