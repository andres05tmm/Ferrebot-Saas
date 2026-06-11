"""F2.3b — selector de documento por venta (ADR 0014): el router /ventas PLUMBEA la intención.

El cajero elige POS/FE al registrar; `crear_venta` reenvía `payload.documento` como `intencion` al
cierre fiscal SOLO en venta nueva. La matriz capacidad×intención la decide `_resolver_documento`
(probada a fondo en test_facturacion_pos_hook); aquí verificamos el cableado del router + que el
documento EFECTIVO (intención reenviada × capacidades del tenant) es el esperado, incluido el caso
"pide FE sin tenerla → cae al default" (que NO rompe la venta).

Patrón test_ventas_listar: app mínima + ASGITransport + dependency_overrides. VentaService y el cierre
fiscal se fakean (no se toca BD): el foco es el plumbing de la intención, no el dominio de ventas.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.facturacion.pos_hook import _resolver_documento
from modules.ventas import router as router_mod
from modules.ventas.router import get_control_stock_estricto, router
from modules.ventas.schemas import VentaLeer
from modules.ventas.service import ResultadoVenta

_CAPS_FE = frozenset({"pos", "pos_electronico", "facturacion_electronica"})   # POS con FE a pedido
_CAPS_SIN_FE = frozenset({"pos", "pos_electronico"})                          # POS sin FE
_PAYLOAD = {"metodo_pago": "efectivo", "lineas": [{"producto_id": 1, "cantidad": 2}]}


def _venta() -> VentaLeer:
    return VentaLeer(
        id=99, consecutivo=1, cliente_id=None, vendedor_id=5,
        fecha=datetime(2026, 6, 10, 10, 0, 0), subtotal=Decimal("10000.00"),
        impuestos=Decimal("0.00"), total=Decimal("10000.00"), metodo_pago="efectivo",
        estado="completada", origen="web", idempotency_key=None,
    )


class _ServiceFake:
    """Reemplaza VentaService: no toca BD; devuelve una venta NUEVA (replay=False)."""

    def __init__(self, *_a, **_k) -> None: ...

    async def registrar_venta(self, datos, vendedor_id, *, control_stock_estricto=False):
        return ResultadoVenta(venta=_venta(), replay=False)


def _app(capturado: dict, *, capacidades: frozenset[str], monkeypatch) -> FastAPI:
    async def _captura_cierre(request, session, venta_id, *, intencion=None):
        capturado["venta_id"] = venta_id
        capturado["intencion"] = intencion

    monkeypatch.setattr(router_mod, "VentaService", _ServiceFake)
    monkeypatch.setattr(router_mod, "encolar_cierre_pos", _captura_cierre)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=5, tenant="pr", rol="vendedor")
    app.dependency_overrides[get_capacidades] = lambda: capacidades
    app.dependency_overrides[get_control_stock_estricto] = lambda: False
    app.dependency_overrides[get_tenant_db] = lambda: None  # la sesión no se usa (service+cierre fakeados)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_documento_fe_en_tenant_con_fe_rutea_fe(monkeypatch):
    cap: dict = {}
    app = _app(cap, capacidades=_CAPS_FE, monkeypatch=monkeypatch)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json={**_PAYLOAD, "documento": "fe"})
    assert r.status_code == 201, r.text
    assert cap["venta_id"] == 99 and cap["intencion"] == "fe"          # el router reenvía la intención
    assert _resolver_documento(_CAPS_FE, cap["intencion"]) == "fe"     # efectivo: FE on-demand


async def test_sin_documento_cae_al_default_por_capacidad(monkeypatch):
    cap: dict = {}
    app = _app(cap, capacidades=_CAPS_FE, monkeypatch=monkeypatch)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json=_PAYLOAD)                 # sin `documento`
    assert r.status_code == 201, r.text
    assert cap["intencion"] is None                                   # None → default por capacidad
    assert _resolver_documento(_CAPS_FE, cap["intencion"]) == "pos"    # POS-default (FE a pedido)


async def test_documento_fe_en_tenant_sin_fe_cae_al_default(monkeypatch):
    cap: dict = {}
    app = _app(cap, capacidades=_CAPS_SIN_FE, monkeypatch=monkeypatch)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json={**_PAYLOAD, "documento": "fe"})
    assert r.status_code == 201, r.text                               # no rompe la venta
    assert cap["intencion"] == "fe"                                   # el router reenvía igual…
    assert _resolver_documento(_CAPS_SIN_FE, cap["intencion"]) == "pos"  # …pero el efectivo cae a POS
