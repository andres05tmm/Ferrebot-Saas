"""Router de trabajadores (vertical construcción, CRUD Fase 1): wiring HTTP con servicio falso.

Patrón test_clientes_router: app mínima + ASGITransport + dependency_overrides. El servicio se inyecta
como fake (CERO red, CERO Postgres); la persistencia real se cubre en integración. Se verifica también
el gate de capacidad `nomina` (sin la feature, el router entero responde 404).
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.trabajadores.errors import TrabajadorDuplicado, TrabajadorInexistente
from modules.trabajadores.router import get_trabajadores_service, router


def _trab(tid: int = 1, documento: str = "123", tipo_vinculacion: str = "DIRECTO", activo: bool = True):
    """Objeto con la forma de Trabajador (from_attributes) para TrabajadorLeer."""
    return SimpleNamespace(
        id=tid, tipo_vinculacion=tipo_vinculacion, documento=documento, tipo_documento="CC",
        nombres="Juan", apellidos="Pérez", cargo="Operador", telefono=None, email=None,
        direccion=None, fecha_ingreso=None, fecha_retiro=None, activo=activo,
        salario_base=None, aplica_aux_transporte=True, eps=None, fondo_pension=None, arl=None,
        caja_compensacion=None, cuenta_bancaria=None, banco_nombre=None, tarifa_hora=None,
        creado_en=datetime(2026, 7, 1, 12, 0, 0), actualizado_en=datetime(2026, 7, 1, 12, 0, 0),
    )


class _FakeTrab:
    """Imita TrabajadoresService: dedup por documento (409), 404 en id ausente, soft delete = olvido."""

    def __init__(self) -> None:
        self._por_id: dict[int, object] = {}
        self._docs: dict[str, object] = {}
        self._next = 1
        self.ultimo_filtro: tuple = ("UNSET", "UNSET")

    async def listar(self, *, tipo_vinculacion=None, activo=None):
        self.ultimo_filtro = (tipo_vinculacion, activo)
        return list(self._por_id.values())

    async def crear(self, datos):
        if datos.documento in self._docs:
            raise TrabajadorDuplicado(datos.documento)
        t = _trab(tid=self._next, documento=datos.documento,
                  tipo_vinculacion=datos.tipo_vinculacion, activo=datos.activo)
        self._por_id[t.id] = t
        self._docs[datos.documento] = t
        self._next += 1
        return t

    async def obtener(self, tid: int):
        t = self._por_id.get(tid)
        if t is None:
            raise TrabajadorInexistente(tid)
        return t

    async def actualizar(self, tid: int, datos):
        t = await self.obtener(tid)
        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(t, campo, valor)
        return t

    async def eliminar(self, tid: int):
        if tid not in self._por_id:
            raise TrabajadorInexistente(tid)
        del self._por_id[tid]   # soft delete visto desde el API: deja de aparecer


def _app(service: _FakeTrab, *, rol: str = "admin", caps=frozenset({"nomina"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_trabajadores_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


def _payload(documento="900", tipo="DIRECTO"):
    return {"tipo_vinculacion": tipo, "documento": documento, "nombres": "Ana",
            "apellidos": "Ruiz", "cargo": "Operador"}


async def test_crear_201_y_dedup_409():
    service = _FakeTrab()
    app = _app(service)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/trabajadores", json=_payload("900"))
        r2 = await c.post("/api/v1/trabajadores", json=_payload("900"))
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text     # documento duplicado


async def test_listar_pasa_los_filtros():
    service = _FakeTrab()
    app = _app(service)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/trabajadores",
                        params={"tipo_vinculacion": "PATACALIENTE", "activo": "false"})
    assert r.status_code == 200, r.text
    assert service.ultimo_filtro == ("PATACALIENTE", False)


async def test_obtener_200_y_404():
    service = _FakeTrab()
    app = _app(service)
    async with _cliente(app) as c:
        creado = (await c.post("/api/v1/trabajadores", json=_payload("1"))).json()
        ok = await c.get(f"/api/v1/trabajadores/{creado['id']}")
        falta = await c.get("/api/v1/trabajadores/9999")
    assert ok.status_code == 200 and ok.json()["documento"] == "1"
    assert falta.status_code == 404


async def test_patch_parcial():
    service = _FakeTrab()
    app = _app(service)
    async with _cliente(app) as c:
        creado = (await c.post("/api/v1/trabajadores", json=_payload("1"))).json()
        r = await c.patch(f"/api/v1/trabajadores/{creado['id']}", json={"cargo": "Maestro de obra"})
    assert r.status_code == 200, r.text
    assert r.json()["cargo"] == "Maestro de obra"


async def test_delete_soft_luego_404():
    """DELETE = soft delete: 204 y después el recurso ya no aparece (404 al pedirlo)."""
    service = _FakeTrab()
    app = _app(service)
    async with _cliente(app) as c:
        creado = (await c.post("/api/v1/trabajadores", json=_payload("1"))).json()
        borrado = await c.delete(f"/api/v1/trabajadores/{creado['id']}")
        luego = await c.get(f"/api/v1/trabajadores/{creado['id']}")
        reborrado = await c.delete(f"/api/v1/trabajadores/{creado['id']}")
    assert borrado.status_code == 204
    assert luego.status_code == 404       # ya no aparece
    assert reborrado.status_code == 404   # baja idempotente hacia el cliente


async def test_gate_nomina_oculta_el_router():
    """Sin la capacidad `nomina`, el router entero responde 404 (como si no existiera)."""
    service = _FakeTrab()
    app = _app(service, caps=frozenset())
    async with _cliente(app) as c:
        r = await c.get("/api/v1/trabajadores")
    assert r.status_code == 404, r.text
