"""GET /api/v1/config — endpoint de arranque del dashboard (api-contract.md): { features, branding, usuario }.

Patrón de test_facturacion_router: app mínima + ASGITransport + dependency_overrides. Las deps
(auth, capacidades, branding) se inyectan; no se toca el control DB real. Sin feature-gate (bootstrap).
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.tenancy.catalogo import NUCLEO
from modules.config.router import Branding, get_branding, router


def _app(caps: frozenset[str], branding: Branding, *, rol: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _caps() -> frozenset[str]:
        return caps

    async def _brand() -> Branding:
        return branding

    app.dependency_overrides[get_capacidades] = _caps
    app.dependency_overrides[get_branding] = _brand
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_config_con_features_y_branding():
    branding = Branding(
        logo_url="http://x/logo.png", color_primario="#000000", nombre_comercial="PR",
        dominio="pr.co", tema="aurora",
    )
    app = _app(frozenset({"facturacion_electronica"}), branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(NUCLEO) <= set(body["features"])                # núcleo siempre
    assert "facturacion_electronica" in body["features"]
    assert body["features"] == sorted(body["features"])        # lista ordenada
    assert body["usuario"]["rol"] == "admin"
    assert body["usuario"]["tenant"] == "pr"
    assert body["branding"]["color_primario"] == "#000000"
    assert body["branding"]["logo_url"] == "http://x/logo.png"
    assert body["branding"]["tema"] == "aurora"                # tema de UI con nombre (white-label)


async def test_config_entrega_tokens_planos_del_preset():
    # El branding viaja YA resuelto: el front recibe tokens planos (no el nombre del preset).
    from core.tenancy.branding_presets import resolver_branding

    resuelto = resolver_branding({"preset": "navaja"})
    branding = Branding(color_primario=resuelto["color_primario"], preset="navaja",
                        tokens=resuelto["tokens"])
    app = _app(frozenset({"pack_agenda"}), branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/config")
    assert r.status_code == 200, r.text
    tokens = r.json()["branding"]["tokens"]
    assert r.json()["branding"]["preset"] == "navaja"
    assert tokens["superficie"] == "#171310"                   # navaja es oscuro
    assert tokens["font_display"] == "Archivo"
    assert set(tokens) >= {"primario", "superficie", "card", "radius", "font_display"}


async def test_config_sin_tokens_compat_front_viejo():
    # /config sin tokens (branding mínimo) NO rompe: tokens viaja como {} y el front cae a su fallback.
    branding = Branding(color_primario="#0d6efd")
    app = _app(frozenset(), branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["branding"]["tokens"] == {}
    assert body["branding"]["preset"] is None
    assert body["branding"]["color_primario"] == "#0d6efd"


async def test_config_sin_branding_defaults_y_solo_nucleo():
    branding = Branding(color_primario="#C8200E")              # empresa sin branding → defaults
    app = _app(frozenset(), branding)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["features"]) == set(NUCLEO)                # solo núcleo
    assert body["branding"]["color_primario"] == "#C8200E"
    assert body["branding"]["logo_url"] is None
    assert body["branding"]["nombre_comercial"] is None
    assert body["branding"]["dominio"] is None
    assert body["branding"]["tema"] is None                    # sin tema → el front cae al base rojo
