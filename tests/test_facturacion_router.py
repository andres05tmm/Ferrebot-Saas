"""E4e RED — gate de feature + router de encolado de factura (HTTP, sin TenantMiddleware).

Pieza pura: `verificar_feature` (404 si falta la capacidad). Router por HTTP con dependency_overrides
+ ASGITransport: 404 sin feature; 201 + crear_pendiente + enqueue cuando la empresa la tiene.
En RED: la pieza pura falla por NotImplementedError; el router responde 500 (no 404/201) y falla.
"""
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades, verificar_feature
from modules.facturacion.repository import FacturaLeer
from modules.facturacion.router import (
    get_enqueuer,
    get_facturacion_service,
    get_tenant_id,
    router,
)

_FACTURA = FacturaLeer(
    id=99, venta_id=10, tipo="factura", prefijo="FPR", consecutivo=1,
    cufe=None, estado="pendiente", idempotency_key="k1", intentos=0,
)


# --- pieza pura --------------------------------------------------------------

def test_verificar_feature_presente():
    verificar_feature("x", frozenset({"x"}))          # no lanza


def test_verificar_feature_ausente():
    with pytest.raises(HTTPException) as exc:
        verificar_feature("x", frozenset())
    assert exc.value.status_code == 404


# --- router por HTTP ---------------------------------------------------------

class _FakeService:
    def __init__(self) -> None:
        self.llamado = None

    async def crear_pendiente(self, venta_id, key):
        self.llamado = (venta_id, key)
        return _FACTURA


class _FakeEnqueuer:
    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    async def enqueue(self, job, *args):
        self.jobs.append((job, *args))


def _app(caps, service, enqueuer) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _caps():
        return caps

    async def _service():
        return service

    async def _enq():
        return enqueuer

    app.dependency_overrides[get_capacidades] = _caps
    app.dependency_overrides[get_facturacion_service] = _service
    app.dependency_overrides[get_enqueuer] = _enq
    app.dependency_overrides[get_tenant_id] = lambda: 7
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="vendedor")
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_sin_feature_404():
    app = _app(frozenset(), _FakeService(), _FakeEnqueuer())
    async with _cliente(app) as c:
        r = await c.post("/api/v1/facturas", json={"venta_id": 10})
    assert r.status_code == 404


async def test_crea_y_encola():
    service, enq = _FakeService(), _FakeEnqueuer()
    app = _app(frozenset({"facturacion_electronica"}), service, enq)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/facturas", json={"venta_id": 10}, headers={"Idempotency-Key": "k1"}
        )
    assert r.status_code == 201
    assert service.llamado == (10, "k1")
    assert enq.jobs == [("emitir_documento", 7, 99)]
    body = r.json()
    assert body["id"] == 99 and body["estado"] == "pendiente"
