"""Wiring HTTP de `POST /obras/{id}/facturar` (Fase 7 DIAN) con servicio FALSO (cero red, cero Postgres).

Patrón de test_obras_fase3_router: app mínima + ASGITransport + dependency_overrides. Cubre el GATE doble
(capacidad `obras` del router + `facturacion_electronica` del endpoint → 404 si falta cualquiera), el gate
de ROL (fiscal = admin; un vendedor recibe 403), el mapeo de errores de dominio a HTTP (404/409) y la forma
de la respuesta (incl. `creada`). La lógica real (venta AIU, CUFE, idempotencia) vive en
test_obras_facturar_integration."""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.facturacion.repository import FacturaLeer
from modules.obra.errors import ObraInexistente, ObraSinCliente, ObraSinCotizacion
from modules.obra.router import get_obras_facturador, router
from modules.obra.service import ResultadoFacturaObra

_CAPS_OK = frozenset({"obras", "facturacion_electronica"})


def _factura(*, estado="pendiente", cufe=None) -> FacturaLeer:
    return FacturaLeer(
        id=42, venta_id=10, tipo="factura", prefijo="FEV", consecutivo=7,
        cufe=cufe, estado=estado, idempotency_key="fe:10", intentos=0,
    )


class _FakeSession:
    """Sesión mínima: `facturar_obra` con `creada=True` la commitea (sin pool ARQ no encola)."""

    async def commit(self) -> None:
        return None


class _FakeObras:
    """Imita `ObrasService.facturar_obra`; parametrizable para forzar cada rama (éxito/idempotente/error)."""

    def __init__(self, *, error=None, creada=True) -> None:
        self._error = error
        self._creada = creada

    async def facturar_obra(self, obra_id, *, vendedor_id):
        if self._error is not None:
            raise self._error
        estado = "pendiente" if self._creada else "aceptada"
        cufe = None if self._creada else "c" * 96
        return ResultadoFacturaObra(factura=_factura(estado=estado, cufe=cufe), creada=self._creada)


def _app(service, *, rol="admin", caps=_CAPS_OK) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_obras_facturador] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    app.dependency_overrides[get_tenant_db] = lambda: _FakeSession()
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_facturar_201_documento_nuevo():
    """Documento NUEVO: 201 con `creada=True` y la forma de `FacturaObraLeer` (el rastro obra→documento)."""
    async with _cliente(_app(_FakeObras(creada=True))) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["obra_id"] == 1
    assert body["factura_id"] == 42
    assert body["venta_id"] == 10
    assert body["tipo"] == "factura"
    assert body["estado"] == "pendiente"
    assert body["creada"] is True


async def test_facturar_201_idempotente_documento_existente():
    """Obra ya facturada: 201 con `creada=False` (devuelve el documento existente, no emite otro CUFE)."""
    async with _cliente(_app(_FakeObras(creada=False))) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["creada"] is False
    assert body["estado"] == "aceptada"
    assert body["cufe"] == "c" * 96


async def test_facturar_requiere_admin():
    """Emisión fiscal: un vendedor no puede facturar la obra (403)."""
    async with _cliente(_app(_FakeObras(), rol="vendedor")) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 403, r.text


async def test_facturar_gate_sin_facturacion_electronica_404():
    """Sin la capacidad `facturacion_electronica` la ruta no existe (404), aunque tenga `obras` y sea admin."""
    async with _cliente(_app(_FakeObras(), caps=frozenset({"obras"}))) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 404, r.text


async def test_facturar_gate_sin_obras_404():
    """Sin la capacidad `obras` el router entero responde 404 (gate del router)."""
    async with _cliente(_app(_FakeObras(), caps=frozenset({"facturacion_electronica"}))) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 404, r.text


async def test_facturar_obra_inexistente_404():
    async with _cliente(_app(_FakeObras(error=ObraInexistente(9)))) as c:
        r = await c.post("/api/v1/obras/9/facturar")
    assert r.status_code == 404, r.text


async def test_facturar_sin_cotizacion_409():
    async with _cliente(_app(_FakeObras(error=ObraSinCotizacion(1)))) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 409, r.text


async def test_facturar_sin_cliente_409():
    async with _cliente(_app(_FakeObras(error=ObraSinCliente(1)))) as c:
        r = await c.post("/api/v1/obras/1/facturar")
    assert r.status_code == 409, r.text
