"""Router de obras (vertical construcción, CRUD Fase 1): wiring HTTP con servicio falso.

Patrón test_clientes_router: app mínima + ASGITransport + dependency_overrides. El servicio se inyecta
como fake (CERO red, CERO Postgres). Cubre CRUD, el detalle con conteos (`ObraResumen`), el mapeo de la
transición inválida a 409, los reportes diarios y el gate de capacidad `obras`. La validación real del
ciclo de vida vive en test_obras_service.py.
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.obra.errors import ObraInexistente, TransicionEstadoInvalida
from modules.obra.repository import ConteosOperacion
from modules.obra.router import get_obras_service, router
from modules.obra.service import _TRANSICIONES


def _obra(oid: int = 1, cliente_id: int = 7, nombre: str = "Vía La Paz", estado: str = "PLANIFICADA"):
    """Objeto con la forma de Obra (from_attributes) para ObraLeer/ObraResumen."""
    return SimpleNamespace(
        id=oid, cotizacion_id=None, cliente_id=cliente_id, nombre=nombre, ubicacion=None,
        fecha_inicio=None, fecha_fin_estimada=None, fecha_fin_real=None, estado=estado, notas=None,
        creado_en=datetime(2026, 7, 1, 12, 0, 0), actualizado_en=datetime(2026, 7, 1, 12, 0, 0),
    )


def _reporte(rid: int, obra_id: int, fecha: date):
    return SimpleNamespace(
        id=rid, obra_id=obra_id, fecha=fecha, reportado_por="Pedro", telegram_user_id=None,
        avance_descripcion="Base granular", m2_ejecutados=None, m3_ejecutados=None, incidentes=None,
        foto_urls=[], origen_registro="MANUAL", creado_en=datetime(2026, 7, 1, 12, 0, 0),
    )


class _FakeObras:
    """Imita ObrasService: 404 en id ausente, transición contra el mapa real, reportes en memoria."""

    def __init__(self) -> None:
        self._por_id: dict[int, object] = {}
        self._reportes: dict[int, list] = {}
        self._next = 1
        self._next_rep = 1
        self.ultimo_filtro: tuple = ("UNSET", "UNSET")

    async def listar(self, *, cliente_id=None, estado=None):
        self.ultimo_filtro = (cliente_id, estado)
        return list(self._por_id.values())

    async def nombres_clientes(self, ids):
        # Azúcar de lectura del listado (cliente_nombre): el fake devuelve un nombre estable por id.
        return {cid: f"Cliente {cid}" for cid in ids}

    async def crear(self, datos):
        o = _obra(oid=self._next, cliente_id=datos.cliente_id, nombre=datos.nombre)
        self._por_id[o.id] = o
        self._next += 1
        return o

    async def obtener(self, oid: int):
        o = self._por_id.get(oid)
        if o is None:
            raise ObraInexistente(oid)
        return o

    async def resumen(self, oid: int):
        o = await self.obtener(oid)
        return o, ConteosOperacion(maquinas_asignadas=1, trabajadores_asignados=2, reportes_diarios=3)

    async def actualizar(self, oid: int, datos):
        o = await self.obtener(oid)
        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(o, campo, valor)
        return o

    async def cambiar_estado(self, oid: int, nuevo: str):
        o = await self.obtener(oid)
        if nuevo not in _TRANSICIONES.get(o.estado, frozenset()):
            raise TransicionEstadoInvalida(o.estado, nuevo)
        o.estado = nuevo
        return o

    async def eliminar(self, oid: int):
        if oid not in self._por_id:
            raise ObraInexistente(oid)
        del self._por_id[oid]

    async def crear_reporte(self, oid: int, datos):
        await self.obtener(oid)
        r = _reporte(self._next_rep, oid, datos.fecha or date(2026, 7, 1))
        self._reportes.setdefault(oid, []).append(r)
        self._next_rep += 1
        return r

    async def listar_reportes(self, oid: int, *, limite: int = 100, offset: int = 0):
        await self.obtener(oid)
        return self._reportes.get(oid, [])[offset : offset + limite]


def _app(service: _FakeObras, *, rol: str = "admin", caps=frozenset({"obras"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_obras_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_crear_201_y_listar_con_filtro():
    service = _FakeObras()
    app = _app(service)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/obras", json={"cliente_id": 7, "nombre": "Vía La Paz"})
        lst = await c.get("/api/v1/obras", params={"cliente_id": 7, "estado": "PLANIFICADA"})
    assert r.status_code == 201, r.text
    assert r.json()["estado"] == "PLANIFICADA"
    assert r.json()["cotizacion_id"] is None       # sin cotización (conversión = Fase 2)
    assert lst.status_code == 200
    assert service.ultimo_filtro == (7, "PLANIFICADA")


async def test_detalle_trae_conteos():
    service = _FakeObras()
    app = _app(service)
    async with _cliente(app) as c:
        oid = (await c.post("/api/v1/obras", json={"cliente_id": 7, "nombre": "X"})).json()["id"]
        r = await c.get(f"/api/v1/obras/{oid}")
        falta = await c.get("/api/v1/obras/9999")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert (cuerpo["maquinas_asignadas"], cuerpo["trabajadores_asignados"], cuerpo["reportes_diarios"]) == (1, 2, 3)
    assert falta.status_code == 404


async def test_transicion_valida_200_e_invalida_409():
    service = _FakeObras()
    app = _app(service)
    async with _cliente(app) as c:
        oid = (await c.post("/api/v1/obras", json={"cliente_id": 7, "nombre": "X"})).json()["id"]
        ok = await c.patch(f"/api/v1/obras/{oid}/estado", json={"estado": "EN_EJECUCION"})
        mala = await c.patch(f"/api/v1/obras/{oid}/estado", json={"estado": "LIQUIDADA"})
    assert ok.status_code == 200 and ok.json()["estado"] == "EN_EJECUCION"
    assert mala.status_code == 409, mala.text     # EN_EJECUCION → LIQUIDADA no permitido


async def test_patch_metadatos():
    service = _FakeObras()
    app = _app(service)
    async with _cliente(app) as c:
        oid = (await c.post("/api/v1/obras", json={"cliente_id": 7, "nombre": "X"})).json()["id"]
        r = await c.patch(f"/api/v1/obras/{oid}", json={"ubicacion": "km 4 vía Suba"})
    assert r.status_code == 200 and r.json()["ubicacion"] == "km 4 vía Suba"


async def test_delete_soft_luego_404():
    service = _FakeObras()
    app = _app(service)
    async with _cliente(app) as c:
        oid = (await c.post("/api/v1/obras", json={"cliente_id": 7, "nombre": "X"})).json()["id"]
        borrado = await c.delete(f"/api/v1/obras/{oid}")
        luego = await c.get(f"/api/v1/obras/{oid}")
    assert borrado.status_code == 204
    assert luego.status_code == 404


async def test_reportes_diarios_crear_y_listar():
    service = _FakeObras()
    app = _app(service)
    async with _cliente(app) as c:
        oid = (await c.post("/api/v1/obras", json={"cliente_id": 7, "nombre": "X"})).json()["id"]
        crear = await c.post(f"/api/v1/obras/{oid}/reportes-diarios",
                             json={"avance_descripcion": "Base granular", "reportado_por": "Pedro"})
        listar = await c.get(f"/api/v1/obras/{oid}/reportes-diarios")
        falta = await c.post("/api/v1/obras/9999/reportes-diarios", json={})
    assert crear.status_code == 201, crear.text
    assert crear.json()["origen_registro"] == "MANUAL"
    assert listar.status_code == 200 and len(listar.json()) == 1
    assert falta.status_code == 404                # reporte sobre obra inexistente


async def test_gate_obras_oculta_el_router():
    service = _FakeObras()
    app = _app(service, caps=frozenset())
    async with _cliente(app) as c:
        r = await c.get("/api/v1/obras")
    assert r.status_code == 404, r.text
