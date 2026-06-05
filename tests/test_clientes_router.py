"""E2 Parte 1 — router de clientes (B2): GET lista, POST (crea/dedup), GET por id.

Patrón test_facturacion_router: app mínima + ASGITransport + dependency_overrides. El servicio se
inyecta como fake (CERO red, CERO Postgres); el filtrado ILIKE real del repo se cubre en integración.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from modules.clientes.router import get_clientes_service, router
from modules.clientes.schemas import ClienteCrear
from modules.clientes.service import ResultadoCliente


def _cli(cid: int = 1, nombre: str = "Ana", documento: str | None = "123"):
    """Objeto con la forma de Cliente (from_attributes); incluye creado_en (requerido por ClienteLeer)."""
    return SimpleNamespace(
        id=cid, nombre=nombre, tipo_documento="CC", documento=documento, telefono=None,
        correo=None, direccion=None, ciudad_dane=None, regimen=None,
        saldo_fiado=Decimal("0"), creado_en=datetime(2026, 6, 4, 12, 0, 0),
    )


class _FakeClientes:
    """Imita ClientesService: dedup por documento, listar (captura q), obtener por id."""

    def __init__(self, listado=None) -> None:
        self._listado = listado or []
        self._docs: dict[str, object] = {}
        self._por_id: dict[int, object] = {}
        self._next = 1
        self.ultimo_q = "UNSET"

    async def listar(self, q: str | None = None):
        self.ultimo_q = q
        return self._listado

    async def crear(self, datos: ClienteCrear) -> ResultadoCliente:
        if datos.documento and datos.documento in self._docs:
            return ResultadoCliente(cliente=self._docs[datos.documento], creado=False)
        c = _cli(cid=self._next, nombre=datos.nombre, documento=datos.documento)
        self._next += 1
        if datos.documento:
            self._docs[datos.documento] = c
        self._por_id[c.id] = c
        return ResultadoCliente(cliente=c, creado=True)

    async def obtener(self, cliente_id: int):
        return self._por_id.get(cliente_id)


def _app(service: _FakeClientes, *, rol: str = "vendedor") -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_clientes_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_listar_sin_q():
    service = _FakeClientes(listado=[_cli(1, "Ana"), _cli(2, "Beto")])
    app = _app(service)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/clientes")
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()] == [1, 2]
    assert service.ultimo_q is None


async def test_listar_con_q_pasa_el_filtro():
    service = _FakeClientes(listado=[_cli(1, "Ana")])
    app = _app(service)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/clientes", params={"q": "an"})
    assert r.status_code == 200, r.text
    assert service.ultimo_q == "an"


async def test_crear_201_y_dedup_200():
    service = _FakeClientes()
    app = _app(service)
    payload = {"nombre": "Ferre La 80", "tipo_documento": "NIT", "documento": "900123456"}
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/clientes", json=payload)
        r2 = await c.post("/api/v1/clientes", json={**payload, "nombre": "La 80 SAS"})
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text                 # dedup por documento → existente
    assert r1.json()["id"] == r2.json()["id"]


async def test_obtener_200_y_404():
    service = _FakeClientes()
    app = _app(service)
    async with _cliente(app) as c:
        creado = (await c.post("/api/v1/clientes", json={"nombre": "Ana", "documento": "1"})).json()
        ok = await c.get(f"/api/v1/clientes/{creado['id']}")
        falta = await c.get("/api/v1/clientes/9999")
    assert ok.status_code == 200, ok.text
    assert ok.json()["nombre"] == "Ana"
    assert falta.status_code == 404
