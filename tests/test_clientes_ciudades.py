"""E6 b2 — catálogos fiscales del form de cliente: GET /clientes/ciudades y /clientes/paises.

Patrón test_facturacion_router: app mínima + ASGITransport + dependency_overrides. El MatiasClient
por empresa se inyecta como fake (CERO red). El gate `require_feature("facturacion_electronica")`
responde 404 si la empresa no tiene la capacidad (feature-flags.md: 'como si no existiera').
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.clientes.router import get_matias_client, router


class _FakeMatias:
    def __init__(self) -> None:
        self.pais_id = None
        self.q = None

    async def listar_ciudades(self, *, pais_id=45, q=""):
        self.pais_id, self.q = pais_id, q
        return [{"matias_id": "149", "dane_code": 5001, "nombre": "Medellín",
                 "departamento": "Antioquia", "pais_id": pais_id}]

    async def listar_paises(self):
        return [{"matias_id": 45, "codigo_a2": "CO", "nombre": "Colombia", "telefono_codigo": "57"}]


def _app(caps: frozenset[str], matias: _FakeMatias) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _caps() -> frozenset[str]:
        return caps

    app.dependency_overrides[get_capacidades] = _caps
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="vendedor")
    app.dependency_overrides[get_matias_client] = lambda: matias
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_ciudades_con_feature_filtra():
    fake = _FakeMatias()
    app = _app(frozenset({"facturacion_electronica"}), fake)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/clientes/ciudades", params={"q": "mede", "pais_id": 45})
    assert r.status_code == 200, r.text
    assert r.json()[0]["nombre"] == "Medellín"
    assert fake.q == "mede" and fake.pais_id == 45      # el q/pais llegan al MatiasClient


async def test_paises_con_feature():
    app = _app(frozenset({"facturacion_electronica"}), _FakeMatias())
    async with _cliente(app) as c:
        r = await c.get("/api/v1/clientes/paises")
    assert r.status_code == 200, r.text
    assert r.json()[0]["codigo_a2"] == "CO"


async def test_sin_feature_gateado():
    # Sin la capacidad fiscal, el gate require_feature oculta el recurso (404).
    app = _app(frozenset(), _FakeMatias())
    async with _cliente(app) as c:
        ciudades = await c.get("/api/v1/clientes/ciudades")
        paises = await c.get("/api/v1/clientes/paises")
    assert ciudades.status_code == 404
    assert paises.status_code == 404
