"""CRUD de asignaciones trabajador→obra (Calendario de obra PIM) — doble capa (espejo de máquina).

(0) AISLAMIENTO multi-tenant (invariante crítico, TEST-PRIMERO): una asignación de la empresa A jamás
    aparece en la B.
(1) Wiring HTTP con servicio FAKE: forma, 404/409 de dominio, roles (GET vendedor OK, POST/PATCH admin) y
    el gate de la capacidad `nomina`.
(2) Integración real (Postgres efímero): defaults y fecha hoy Colombia, solape (un trabajador no en dos
    obras a la vez), y el evento SSE `asignacion_trabajador_actualizada` (espía publish). Sin dinero ni
    transición de estado (el trabajador no lleva estado).
"""
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.trabajadores.repository as trabajadores_repo
from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from modules.trabajadores.errors import (
    AsignacionInexistente,
    AsignacionSolapada,
    ObraNoAsignable,
    TrabajadorInexistente,
)
from modules.trabajadores.repository import SqlTrabajadoresRepository
from modules.trabajadores.router import get_trabajadores_service, router
from modules.trabajadores.schemas import (
    AsignacionTrabajadorActualizar,
    AsignacionTrabajadorCrear,
    TrabajadorCrear,
)
from modules.trabajadores.service import TrabajadoresService


def _service(session: AsyncSession) -> TrabajadoresService:
    return TrabajadoresService(SqlTrabajadoresRepository(session))


def _trabajador(**over) -> TrabajadorCrear:
    base = {
        "tipo_vinculacion": "PATACALIENTE",
        "documento": "1001",
        "nombres": "Juan",
        "apellidos": "Pérez",
        "cargo": "Operador",
        "tarifa_hora": 15000,
    }
    base.update(over)
    return TrabajadorCrear(**base)


async def _cliente_obra(s: AsyncSession, *, estado: str = "EN_EJECUCION") -> int:
    cid = (
        await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Alcaldía') RETURNING id"))
    ).scalar_one()
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'Vía Llanogrande', :e) RETURNING id"),
            {"c": cid, "e": estado},
        )
    ).scalar_one()


def _espia_publish(monkeypatch) -> list[tuple]:
    eventos: list[tuple] = []

    async def fake(session, event, data):
        eventos.append((event, data))

    monkeypatch.setattr(trabajadores_repo, "publish", fake)
    return eventos


# =====================================================================================================
# (0) AISLAMIENTO multi-tenant — invariante crítico, escrito PRIMERO
# =====================================================================================================
async def _contar(engine) -> int:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT count(*) FROM asignaciones_trabajador_obra"))
        ).scalar_one()


async def test_empresa_A_no_ve_asignaciones_de_empresa_B(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine) as sa:
        t = await _service(sa).crear(_trabajador())
        oid = await _cliente_obra(sa)
        await _service(sa).crear_asignacion(t.id, AsignacionTrabajadorCrear(obra_id=oid))
        await sa.commit()

    assert await _contar(empresa_a.engine) == 1
    assert await _contar(empresa_b.engine) == 0


# =====================================================================================================
# (1) Wiring HTTP con servicio FAKE
# =====================================================================================================
def _asig(**over) -> SimpleNamespace:
    base = dict(
        id=1, trabajador_id=1, obra_id=2, fecha_inicio=date(2026, 7, 1), fecha_fin=None, activa=True
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeTrab:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def listar_asignaciones(self, trabajador_id):
        if self._error is not None:
            raise self._error
        return [_asig(trabajador_id=trabajador_id)]

    async def crear_asignacion(self, trabajador_id, datos):
        if self._error is not None:
            raise self._error
        return _asig(trabajador_id=trabajador_id, obra_id=datos.obra_id)

    async def actualizar_asignacion(self, trabajador_id, asignacion_id, datos):
        if self._error is not None:
            raise self._error
        return _asig(id=asignacion_id, trabajador_id=trabajador_id)


def _app(service, *, rol="admin", caps=frozenset({"nomina"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_trabajadores_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente_http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_listar_200_forma_vendedor():
    """El GET de asignaciones lo puede ver el vendedor (personal de campo)."""
    async with _cliente_http(_app(_FakeTrab(), rol="vendedor")) as c:
        r = await c.get("/api/v1/trabajadores/1/asignaciones")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["trabajador_id"] == 1
    assert body[0]["activa"] is True
    assert "precio_hora" not in body[0]      # sin dinero en el espejo trabajador


async def test_crear_201_forma():
    async with _cliente_http(_app(_FakeTrab())) as c:
        r = await c.post("/api/v1/trabajadores/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 201, r.text
    assert r.json()["obra_id"] == 2


async def test_patch_200_forma():
    async with _cliente_http(_app(_FakeTrab())) as c:
        r = await c.patch("/api/v1/trabajadores/1/asignaciones/9", json={"activa": False})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == 9


async def test_crear_403_vendedor():
    async with _cliente_http(_app(_FakeTrab(), rol="vendedor")) as c:
        r = await c.post("/api/v1/trabajadores/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 403, r.text


async def test_patch_403_vendedor():
    async with _cliente_http(_app(_FakeTrab(), rol="vendedor")) as c:
        r = await c.patch("/api/v1/trabajadores/1/asignaciones/9", json={"activa": False})
    assert r.status_code == 403, r.text


async def test_gateado_por_nomina():
    async with _cliente_http(_app(_FakeTrab(), caps=frozenset())) as c:
        r = await c.get("/api/v1/trabajadores/1/asignaciones")
    assert r.status_code == 404, r.text


async def test_404_trabajador_inexistente():
    async with _cliente_http(_app(_FakeTrab(error=TrabajadorInexistente(999)))) as c:
        r = await c.post("/api/v1/trabajadores/999/asignaciones", json={"obra_id": 2})
    assert r.status_code == 404, r.text


async def test_404_obra_inexistente():
    async with _cliente_http(_app(_FakeTrab(error=ObraNoAsignable(999, "inexistente")))) as c:
        r = await c.post("/api/v1/trabajadores/1/asignaciones", json={"obra_id": 999})
    assert r.status_code == 404, r.text


async def test_409_obra_liquidada():
    async with _cliente_http(_app(_FakeTrab(error=ObraNoAsignable(2, "liquidada")))) as c:
        r = await c.post("/api/v1/trabajadores/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 409, r.text


async def test_409_solape():
    async with _cliente_http(_app(_FakeTrab(error=AsignacionSolapada(1, date(2026, 7, 1), None)))) as c:
        r = await c.post("/api/v1/trabajadores/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 409, r.text


async def test_patch_404_asignacion_inexistente():
    async with _cliente_http(_app(_FakeTrab(error=AsignacionInexistente(999)))) as c:
        r = await c.patch("/api/v1/trabajadores/1/asignaciones/999", json={"activa": False})
    assert r.status_code == 404, r.text


# =====================================================================================================
# (2) Integración real (Postgres efímero)
# =====================================================================================================
async def test_crear_fecha_default_hoy(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        oid = await _cliente_obra(s)
        asig = await svc.crear_asignacion(t.id, AsignacionTrabajadorCrear(obra_id=oid))
        assert asig.fecha_inicio == today_co()
        assert asig.activa is True


async def test_crear_trabajador_eliminado_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        oid = await _cliente_obra(s)
        await svc.eliminar(t.id)
        with pytest.raises(TrabajadorInexistente):
            await svc.crear_asignacion(t.id, AsignacionTrabajadorCrear(obra_id=oid))


async def test_crear_obra_liquidada_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        oid = await _cliente_obra(s, estado="LIQUIDADA")
        with pytest.raises(ObraNoAsignable) as exc:
            await svc.crear_asignacion(t.id, AsignacionTrabajadorCrear(obra_id=oid))
        assert exc.value.motivo == "liquidada"


async def test_solape_un_trabajador_no_en_dos_obras(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('C') RETURNING id"))
        ).scalar_one()

        async def _obra() -> int:
            return (
                await s.execute(
                    text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'O', 'EN_EJECUCION') RETURNING id"),
                    {"c": cid},
                )
            ).scalar_one()

        o1 = await _obra()
        o2 = await _obra()
        await svc.crear_asignacion(
            t.id, AsignacionTrabajadorCrear(obra_id=o1, fecha_inicio=date(2026, 7, 1), fecha_fin=date(2026, 7, 10))
        )
        with pytest.raises(AsignacionSolapada):
            await svc.crear_asignacion(
                t.id, AsignacionTrabajadorCrear(obra_id=o2, fecha_inicio=date(2026, 7, 5), fecha_fin=date(2026, 7, 20))
            )


async def test_reactivar_con_solape_409(tenant):
    """Cerrar A, crear B sobre el rango viejo de A y REACTIVAR A debe chocar (revalida el solape)."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('C') RETURNING id"))
        ).scalar_one()

        async def _obra() -> int:
            return (
                await s.execute(
                    text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'O', 'EN_EJECUCION') RETURNING id"),
                    {"c": cid},
                )
            ).scalar_one()

        o1 = await _obra()
        o2 = await _obra()
        a = await svc.crear_asignacion(
            t.id, AsignacionTrabajadorCrear(obra_id=o1, fecha_inicio=date(2026, 7, 1), fecha_fin=date(2026, 7, 31))
        )
        await svc.actualizar_asignacion(t.id, a.id, AsignacionTrabajadorActualizar(activa=False))
        await svc.crear_asignacion(
            t.id, AsignacionTrabajadorCrear(obra_id=o2, fecha_inicio=date(2026, 7, 10), fecha_fin=date(2026, 7, 20))
        )
        with pytest.raises(AsignacionSolapada):
            await svc.actualizar_asignacion(t.id, a.id, AsignacionTrabajadorActualizar(activa=True))


def test_crear_fecha_fin_pasada_sin_inicio_invalida():
    """Sin fecha_inicio el default efectivo es HOY Colombia: fecha_fin en el pasado = rango invertido (422)."""
    with pytest.raises(ValidationError):
        AsignacionTrabajadorCrear(obra_id=1, fecha_fin=today_co() - timedelta(days=1))


async def test_listar_orden_reciente_primero(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('C') RETURNING id"))
        ).scalar_one()

        async def _obra() -> int:
            return (
                await s.execute(
                    text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'O', 'EN_EJECUCION') RETURNING id"),
                    {"c": cid},
                )
            ).scalar_one()

        o1 = await _obra()
        o2 = await _obra()
        await svc.crear_asignacion(
            t.id, AsignacionTrabajadorCrear(obra_id=o1, fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 1, 31))
        )
        await svc.crear_asignacion(
            t.id, AsignacionTrabajadorCrear(obra_id=o2, fecha_inicio=date(2026, 6, 1))
        )
        lista = await svc.listar_asignaciones(t.id)
        assert [a.obra_id for a in lista] == [o2, o1]   # fecha_inicio DESC


async def test_patch_asignacion_de_otro_trabajador_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t1 = await svc.crear(_trabajador(documento="1001"))
        t2 = await svc.crear(_trabajador(documento="1002"))
        oid = await _cliente_obra(s)
        asig = await svc.crear_asignacion(t1.id, AsignacionTrabajadorCrear(obra_id=oid))
        with pytest.raises(AsignacionInexistente):
            await svc.actualizar_asignacion(
                t2.id, asig.id, AsignacionTrabajadorActualizar(activa=False)
            )


async def test_evento_asignacion_trabajador_actualizada(tenant, monkeypatch):
    eventos = _espia_publish(monkeypatch)
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        t = await svc.crear(_trabajador())
        oid = await _cliente_obra(s)
        asig = await svc.crear_asignacion(t.id, AsignacionTrabajadorCrear(obra_id=oid))
        await svc.actualizar_asignacion(
            t.id, asig.id, AsignacionTrabajadorActualizar(activa=False)
        )

    nombres = [e for e, _ in eventos]
    assert nombres.count("asignacion_trabajador_actualizada") == 2
    payload = eventos[0][1]
    assert payload["trabajador_id"] == t.id
    assert payload["obra_id"] == oid
