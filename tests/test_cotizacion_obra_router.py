"""Router del cotizador AIU: wiring HTTP con servicio falso (CERO red, CERO Postgres).

Patrón test_obras_router: app mínima + ASGITransport + dependency_overrides. Cubre CRUD, el desglose
AIU en la lectura, el mapeo de la transición inválida a 409, la descarga de Excel, el gate de rol
`admin` de la conversión, su 409 cuando la cotización no está GANADA y el gate de capacidad
`cotizaciones_aiu`. La validación real del ciclo de vida vive en test_cotizacion_obra_service.py.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.cotizacion_obra.errors import (
    CotizacionInexistente,
    CotizacionNoGanada,
    TransicionEstadoInvalida,
)
from modules.cotizacion_obra.router import get_cotizacion_obra_service, router
from modules.cotizacion_obra.service import CotizacionArmada, _TRANSICIONES
from services.calculations.aiu import calcular_totales_cotizacion

_TS = datetime(2026, 7, 1, 12, 0, 0)


def _item(iid: int, orden: int, cantidad="1000", valor="10000"):
    return SimpleNamespace(
        id=iid, orden=orden, descripcion="Base granular", unidad="m3",
        cantidad=Decimal(cantidad), valor_unitario=Decimal(valor),
        costo_material_est=None, costo_mano_obra_est=None, costo_equipo_est=None,
    )


def _cot_ns(cid: int, numero: str, estado: str):
    return SimpleNamespace(
        id=cid, numero=numero, cliente_id=7, nombre_obra="Vía La Paz", ubicacion=None,
        fecha_emision=_TS, vigencia_dias=15,
        administracion_pct=Decimal("0.05"), imprevistos_pct=Decimal("0.03"),
        utilidad_pct=Decimal("0.04"), iva_sobre_utilidad_pct=Decimal("0.19"),
        estado=estado, condiciones=None, creado_en=_TS, actualizado_en=_TS,
    )


def _armada(cot, items) -> CotizacionArmada:
    tot = calcular_totales_cotizacion(
        items,
        administracion_pct=cot.administracion_pct,
        imprevistos_pct=cot.imprevistos_pct,
        utilidad_pct=cot.utilidad_pct,
        iva_sobre_utilidad_pct=cot.iva_sobre_utilidad_pct,
    )
    return CotizacionArmada(cot, items, tot)


def _obra(cotizacion_id: int):
    return SimpleNamespace(
        id=900 + cotizacion_id, cotizacion_id=cotizacion_id, cliente_id=7, nombre="Vía La Paz",
        ubicacion=None, fecha_inicio=None, fecha_fin_estimada=None, fecha_fin_real=None,
        estado="PLANIFICADA", notas=None, creado_en=_TS, actualizado_en=_TS,
    )


class _FakeCotizService:
    """Imita CotizacionObraService: numera, valida transiciones contra el mapa real, convierte GANADAs."""

    def __init__(self) -> None:
        self._store: dict[int, tuple] = {}
        self._next = 1
        self.ultimo_filtro: tuple = ("UNSET", "UNSET")
        self.convertidas: list[int] = []

    async def crear(self, datos):
        cid = self._next
        self._next += 1
        cot = _cot_ns(cid, f"PIM-{cid:03d}-2026", "BORRADOR")
        items = [
            _item(i + 1, it.orden, str(it.cantidad), str(it.valor_unitario))
            for i, it in enumerate(datos.items)
        ]
        self._store[cid] = (cot, items)
        return _armada(cot, items)

    async def obtener(self, cid):
        if cid not in self._store:
            raise CotizacionInexistente(cid)
        cot, items = self._store[cid]
        return _armada(cot, items)

    async def listar(self, *, estado=None, cliente_id=None):
        self.ultimo_filtro = (estado, cliente_id)
        return [_armada(c, i) for c, i in self._store.values()]

    async def actualizar(self, cid, datos):
        if cid not in self._store:
            raise CotizacionInexistente(cid)
        cot, items = self._store[cid]
        if datos.nombre_obra is not None:
            cot.nombre_obra = datos.nombre_obra
        return _armada(cot, items)

    async def cambiar_estado(self, cid, nuevo):
        if cid not in self._store:
            raise CotizacionInexistente(cid)
        cot, items = self._store[cid]
        if nuevo not in _TRANSICIONES.get(cot.estado, frozenset()):
            raise TransicionEstadoInvalida(cot.estado, nuevo)
        cot.estado = nuevo
        return _armada(cot, items)

    async def convertir_a_obra(self, cid):
        if cid not in self._store:
            raise CotizacionInexistente(cid)
        cot, _ = self._store[cid]
        if cot.estado != "GANADA":
            raise CotizacionNoGanada(cot.estado)
        self.convertidas.append(cid)
        return _obra(cid)


def _app(service: _FakeCotizService, *, rol: str = "admin", caps=frozenset({"cotizaciones_aiu"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_cotizacion_obra_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


_ITEMS = [{"orden": 1, "descripcion": "Base", "unidad": "m3", "cantidad": "1000", "valor_unitario": "10000"}]


async def test_crear_201_con_numero_y_desglose_aiu():
    service = _FakeCotizService()
    async with _cliente(_app(service)) as c:
        r = await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "Vía", "items": _ITEMS,
                                                            "administracion_pct": "0.05", "imprevistos_pct": "0.03",
                                                            "utilidad_pct": "0.04"})
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["numero"] == "PIM-001-2026"
    assert cuerpo["estado"] == "BORRADOR"
    # el desglose AIU viaja en la lectura (IVA sólo sobre la utilidad)
    assert cuerpo["totales"]["total"] == "11276000.00"
    assert cuerpo["totales"]["iva_utilidad"] == "76000.00"
    assert Decimal(cuerpo["items"][0]["subtotal"]) == Decimal("10000000")   # cantidad × valor_unitario


async def test_listar_con_filtro():
    service = _FakeCotizService()
    async with _cliente(_app(service)) as c:
        await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "Vía", "items": _ITEMS})
        lst = await c.get("/api/v1/cotizaciones-obra", params={"estado": "BORRADOR", "cliente_id": 7})
    assert lst.status_code == 200
    assert service.ultimo_filtro == ("BORRADOR", 7)
    assert lst.json()[0]["total"] == "11276000.00"


async def test_detalle_404_si_no_existe():
    service = _FakeCotizService()
    async with _cliente(_app(service)) as c:
        r = await c.get("/api/v1/cotizaciones-obra/9999")
    assert r.status_code == 404


async def test_transicion_valida_200_e_invalida_409():
    service = _FakeCotizService()
    async with _cliente(_app(service)) as c:
        cid = (await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "X", "items": _ITEMS})).json()["id"]
        ok = await c.post(f"/api/v1/cotizaciones-obra/{cid}/estado", json={"estado": "ENVIADA"})
        mala = await c.post(f"/api/v1/cotizaciones-obra/{cid}/estado", json={"estado": "BORRADOR"})
    assert ok.status_code == 200 and ok.json()["estado"] == "ENVIADA"
    assert mala.status_code == 409, mala.text   # ENVIADA → BORRADOR no permitido


async def test_editar_put_cambia_nombre():
    service = _FakeCotizService()
    async with _cliente(_app(service)) as c:
        cid = (await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "X", "items": _ITEMS})).json()["id"]
        r = await c.put(f"/api/v1/cotizaciones-obra/{cid}", json={"nombre_obra": "Vía nueva"})
    assert r.status_code == 200 and r.json()["nombre_obra"] == "Vía nueva"


async def test_exportar_excel_descarga_xlsx():
    service = _FakeCotizService()
    async with _cliente(_app(service)) as c:
        cid = (await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "X", "items": _ITEMS})).json()["id"]
        r = await c.get(f"/api/v1/cotizaciones-obra/{cid}/exportar-excel")
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.headers["content-disposition"].endswith('.xlsx"')
    assert r.content[:2] == b"PK"   # magic de un .xlsx (zip)


async def test_convertir_requiere_ganada_y_rol_admin():
    service = _FakeCotizService()
    # como vendedor: el gate de rol admin responde 403 antes de tocar la lógica
    async with _cliente(_app(service, rol="vendedor")) as c:
        cid = (await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "X", "items": _ITEMS})).json()["id"]
        prohibido = await c.post(f"/api/v1/cotizaciones-obra/{cid}/convertir-obra")
    assert prohibido.status_code == 403, prohibido.text

    # como admin: sin estar GANADA → 409; tras GANARla → 200 con la obra PLANIFICADA
    service2 = _FakeCotizService()
    async with _cliente(_app(service2, rol="admin")) as c:
        cid = (await c.post("/api/v1/cotizaciones-obra", json={"cliente_id": 7, "nombre_obra": "X", "items": _ITEMS})).json()["id"]
        no_ganada = await c.post(f"/api/v1/cotizaciones-obra/{cid}/convertir-obra")
        await c.post(f"/api/v1/cotizaciones-obra/{cid}/estado", json={"estado": "ENVIADA"})
        await c.post(f"/api/v1/cotizaciones-obra/{cid}/estado", json={"estado": "GANADA"})
        ok = await c.post(f"/api/v1/cotizaciones-obra/{cid}/convertir-obra")
    assert no_ganada.status_code == 409, no_ganada.text
    assert ok.status_code == 200, ok.text
    assert ok.json()["estado"] == "PLANIFICADA" and ok.json()["cotizacion_id"] == cid


async def test_gate_cotizaciones_aiu_oculta_el_router():
    service = _FakeCotizService()
    async with _cliente(_app(service, caps=frozenset())) as c:
        r = await c.get("/api/v1/cotizaciones-obra")
    assert r.status_code == 404, r.text
