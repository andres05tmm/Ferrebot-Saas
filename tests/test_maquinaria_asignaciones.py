"""CRUD de asignaciones máquina→obra (Calendario de obra PIM) — doble capa.

(0) AISLAMIENTO multi-tenant (invariante crítico, TEST-PRIMERO): una asignación dada de alta en la
    empresa A JAMÁS aparece al consultar la B (la base ES la frontera; no hay `empresa_id`).
(1) Wiring HTTP con servicio FAKE (patrón `test_maquinaria_mantenimientos.py`): forma de la respuesta,
    404 de máquina/obra/operador inexistente, 409 de obra LIQUIDADA y solape, 403 del vendedor y gate.
(2) Integración real contra Postgres efímero: defaults de la máquina y fecha hoy Colombia, solape en sus
    tres formas, transición de estado DISPONIBLE↔OCUPADA y los eventos SSE del calendario (espía publish).
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.repository as maquinaria_repo
from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from modules.maquinaria.errors import (
    AsignacionInexistente,
    AsignacionSolapada,
    MaquinaInexistente,
    ObraNoAsignable,
    OperadorInexistente,
)
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.router import get_maquinaria_service, router
from modules.maquinaria.schemas import (
    AsignacionMaquinaActualizar,
    AsignacionMaquinaCrear,
    MaquinaCrear,
    RegistroHorasCrear,
)
from modules.maquinaria.service import MaquinariaService


def _service(session: AsyncSession) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(session))


def _maquina(**over) -> MaquinaCrear:
    base = {
        "codigo": "M-001",
        "nombre": "Retroexcavadora CAT 416",
        "tipo": "retroexcavadora",
        "precio_hora_default": Decimal("150000"),
        "minimo_horas_factura": 4,
    }
    base.update(over)
    return MaquinaCrear(**base)


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Alcaldía') RETURNING id"))
    ).scalar_one()


async def _obra(s: AsyncSession, cid: int, *, estado: str = "EN_EJECUCION") -> int:
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'Vía Llanogrande', :e) RETURNING id"),
            {"c": cid, "e": estado},
        )
    ).scalar_one()


def _espia_publish(monkeypatch) -> list[tuple]:
    """Espía `modules.maquinaria.repository.publish` (el repo lo importa por nombre de módulo)."""
    eventos: list[tuple] = []

    async def fake(session, event, data):
        eventos.append((event, data))

    monkeypatch.setattr(maquinaria_repo, "publish", fake)
    return eventos


# =====================================================================================================
# (0) AISLAMIENTO multi-tenant — invariante crítico, escrito PRIMERO
# =====================================================================================================
async def _contar_asignaciones(engine) -> int:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT count(*) FROM asignaciones_maquina_obra"))
        ).scalar_one()


async def test_empresa_A_no_ve_asignaciones_de_empresa_B(tenant_factory):
    """Una asignación asentada en la empresa A jamás aparece en la B (la frontera es la base)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine) as sa:
        maquina = await _service(sa).crear(_maquina())
        cid = await _cliente(sa)
        oid = await _obra(sa, cid)
        await _service(sa).crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        await sa.commit()

    assert await _contar_asignaciones(empresa_a.engine) == 1
    assert await _contar_asignaciones(empresa_b.engine) == 0


# =====================================================================================================
# (1) Wiring HTTP con servicio FAKE
# =====================================================================================================
def _asig(**over) -> SimpleNamespace:
    base = dict(
        id=1, maquina_id=1, obra_id=2, fecha_inicio=date(2026, 7, 1), fecha_fin=None,
        precio_hora=Decimal("150000.00"), minimo_horas=4, operador_id=3, activa=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeMaquinaria:
    """Fake del `MaquinariaService` para el wiring HTTP (sin BD): fuerza los errores de dominio."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def crear_asignacion(self, maquina_id, datos):
        if self._error is not None:
            raise self._error
        return _asig(maquina_id=maquina_id, obra_id=datos.obra_id)

    async def actualizar_asignacion(self, maquina_id, asignacion_id, datos):
        if self._error is not None:
            raise self._error
        return _asig(id=asignacion_id, maquina_id=maquina_id)


def _app(service, *, rol="admin", caps=frozenset({"maquinaria"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_maquinaria_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente_http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_crear_201_forma():
    async with _cliente_http(_app(_FakeMaquinaria())) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["maquina_id"] == 1
    assert body["obra_id"] == 2
    assert body["precio_hora"] == "150000.00"      # Decimal serializa como string
    assert body["activa"] is True


async def test_patch_200_forma():
    async with _cliente_http(_app(_FakeMaquinaria())) as c:
        r = await c.patch("/api/v1/maquinas/1/asignaciones/9", json={"activa": False})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == 9


async def test_crear_403_vendedor():
    async with _cliente_http(_app(_FakeMaquinaria(), rol="vendedor")) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 403, r.text


async def test_patch_403_vendedor():
    async with _cliente_http(_app(_FakeMaquinaria(), rol="vendedor")) as c:
        r = await c.patch("/api/v1/maquinas/1/asignaciones/9", json={"activa": False})
    assert r.status_code == 403, r.text


async def test_gateado_por_maquinaria():
    async with _cliente_http(_app(_FakeMaquinaria(), caps=frozenset())) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 404, r.text


async def test_404_maquina_inexistente():
    fake = _FakeMaquinaria(error=MaquinaInexistente(999))
    async with _cliente_http(_app(fake)) as c:
        r = await c.post("/api/v1/maquinas/999/asignaciones", json={"obra_id": 2})
    assert r.status_code == 404, r.text


async def test_404_obra_inexistente():
    fake = _FakeMaquinaria(error=ObraNoAsignable(999, "inexistente"))
    async with _cliente_http(_app(fake)) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 999})
    assert r.status_code == 404, r.text


async def test_409_obra_liquidada():
    fake = _FakeMaquinaria(error=ObraNoAsignable(2, "liquidada"))
    async with _cliente_http(_app(fake)) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 409, r.text


async def test_404_operador_inexistente():
    fake = _FakeMaquinaria(error=OperadorInexistente(77))
    async with _cliente_http(_app(fake)) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 2, "operador_id": 77})
    assert r.status_code == 404, r.text


async def test_409_solape():
    fake = _FakeMaquinaria(error=AsignacionSolapada(1, date(2026, 7, 1), None))
    async with _cliente_http(_app(fake)) as c:
        r = await c.post("/api/v1/maquinas/1/asignaciones", json={"obra_id": 2})
    assert r.status_code == 409, r.text


async def test_patch_404_asignacion_inexistente():
    fake = _FakeMaquinaria(error=AsignacionInexistente(999))
    async with _cliente_http(_app(fake)) as c:
        r = await c.patch("/api/v1/maquinas/1/asignaciones/999", json={"activa": False})
    assert r.status_code == 404, r.text


async def test_crear_422_rango_invertido():
    """El validador del schema rechaza fecha_fin < fecha_inicio (422 sin llegar al service)."""
    async with _cliente_http(_app(_FakeMaquinaria())) as c:
        r = await c.post(
            "/api/v1/maquinas/1/asignaciones",
            json={"obra_id": 2, "fecha_inicio": "2026-07-10", "fecha_fin": "2026-07-01"},
        )
    assert r.status_code == 422, r.text


# =====================================================================================================
# (2) Integración real (Postgres efímero)
# =====================================================================================================
async def test_crear_defaults_de_la_maquina_y_fecha_hoy(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(precio_hora_default=Decimal("180000"), minimo_horas_factura=6))
        cid = await _cliente(s)
        oid = await _obra(s, cid)

        asig = await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        assert asig.fecha_inicio == today_co()          # default hoy Colombia (regla #4)
        assert asig.precio_hora == Decimal("180000")    # default de la máquina
        assert asig.minimo_horas == 6
        assert asig.activa is True


async def test_crear_precio_y_minimo_explicitos_ganan(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        asig = await svc.crear_asignacion(
            maquina.id,
            AsignacionMaquinaCrear(obra_id=oid, precio_hora=Decimal("200000"), minimo_horas=2),
        )
        assert asig.precio_hora == Decimal("200000")
        assert asig.minimo_horas == 2


async def test_crear_maquina_eliminada_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        await svc.eliminar(maquina.id)   # soft delete
        with pytest.raises(MaquinaInexistente):
            await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))


async def test_crear_obra_inexistente(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        with pytest.raises(ObraNoAsignable) as exc:
            await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=999999))
        assert exc.value.motivo == "inexistente"


async def test_crear_obra_liquidada_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid, estado="LIQUIDADA")
        with pytest.raises(ObraNoAsignable) as exc:
            await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        assert exc.value.motivo == "liquidada"


async def test_crear_operador_inexistente(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        with pytest.raises(OperadorInexistente):
            await svc.crear_asignacion(
                maquina.id, AsignacionMaquinaCrear(obra_id=oid, operador_id=999999)
            )


async def test_solape_rango_identico(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        o2 = await _obra(s, cid)
        await svc.crear_asignacion(
            maquina.id,
            AsignacionMaquinaCrear(obra_id=oid, fecha_inicio=date(2026, 7, 1), fecha_fin=date(2026, 7, 10)),
        )
        with pytest.raises(AsignacionSolapada):
            await svc.crear_asignacion(
                maquina.id,
                AsignacionMaquinaCrear(obra_id=o2, fecha_inicio=date(2026, 7, 1), fecha_fin=date(2026, 7, 10)),
            )


async def test_solape_parcial(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        o2 = await _obra(s, cid)
        await svc.crear_asignacion(
            maquina.id,
            AsignacionMaquinaCrear(obra_id=oid, fecha_inicio=date(2026, 7, 1), fecha_fin=date(2026, 7, 10)),
        )
        with pytest.raises(AsignacionSolapada):
            await svc.crear_asignacion(
                maquina.id,
                AsignacionMaquinaCrear(obra_id=o2, fecha_inicio=date(2026, 7, 8), fecha_fin=date(2026, 7, 20)),
            )


async def test_solape_fecha_fin_null_infinito(tenant):
    """Una asignación abierta (fecha_fin NULL) cubre desde su inicio hasta infinito: bloquea toda nueva
    que arranque después."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        o2 = await _obra(s, cid)
        await svc.crear_asignacion(
            maquina.id, AsignacionMaquinaCrear(obra_id=oid, fecha_inicio=date(2026, 7, 1))
        )
        with pytest.raises(AsignacionSolapada):
            await svc.crear_asignacion(
                maquina.id,
                AsignacionMaquinaCrear(obra_id=o2, fecha_inicio=date(2026, 12, 1), fecha_fin=None),
            )


async def test_solape_cerrada_no_bloquea_posterior(tenant):
    """Una asignación con fecha_fin pasada NO bloquea una nueva que arranca después (sin cruce)."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        o2 = await _obra(s, cid)
        await svc.crear_asignacion(
            maquina.id,
            AsignacionMaquinaCrear(obra_id=oid, fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 1, 31)),
        )
        # No debe lanzar: arranca el 2026-02-01, después del cierre.
        asig = await svc.crear_asignacion(
            maquina.id, AsignacionMaquinaCrear(obra_id=o2, fecha_inicio=date(2026, 2, 1))
        )
        assert asig.id is not None


async def test_disponible_a_ocupada_al_asignar_hoy(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(estado="DISPONIBLE"))
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        recargada = await svc.obtener(maquina.id)
        assert recargada.estado == "OCUPADA"


async def test_no_transiciona_si_mantenimiento(tenant):
    """Asignar una máquina en MANTENIMIENTO NO la pasa a OCUPADA (el mantenimiento manda)."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(estado="MANTENIMIENTO"))
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        recargada = await svc.obtener(maquina.id)
        assert recargada.estado == "MANTENIMIENTO"


async def test_asignacion_futura_no_ocupa_hoy(tenant):
    """Una asignación que arranca mañana NO ocupa la máquina hoy."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(estado="DISPONIBLE"))
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        await svc.crear_asignacion(
            maquina.id,
            AsignacionMaquinaCrear(obra_id=oid, fecha_inicio=today_co() + timedelta(days=1)),
        )
        recargada = await svc.obtener(maquina.id)
        assert recargada.estado == "DISPONIBLE"


async def test_patch_cierra_vuelve_a_disponible(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(estado="DISPONIBLE"))
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        asig = await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        assert (await svc.obtener(maquina.id)).estado == "OCUPADA"

        await svc.actualizar_asignacion(
            maquina.id, asig.id, AsignacionMaquinaActualizar(activa=False)
        )
        assert (await svc.obtener(maquina.id)).estado == "DISPONIBLE"


async def test_patch_cierra_pero_otra_vigente_no_libera(tenant):
    """Si al cerrar una asignación queda OTRA activa vigente hoy, la máquina sigue OCUPADA.

    Se insertan dos asignaciones activas solapadas por SQL directo (el service las bloquearía): simula
    datos legado/ETL. Cerrar una por el service no debe liberar la máquina."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(estado="OCUPADA"))
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        hoy = today_co()

        async def _asig_sql() -> int:
            return (
                await s.execute(
                    text(
                        "INSERT INTO asignaciones_maquina_obra "
                        "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas, activa) "
                        "VALUES (:m, :o, :f, 100000, 1, true) RETURNING id"
                    ),
                    {"m": maquina.id, "o": oid, "f": hoy},
                )
            ).scalar_one()

        a1 = await _asig_sql()
        await _asig_sql()   # segunda activa vigente hoy
        await s.flush()

        await svc.actualizar_asignacion(maquina.id, a1, AsignacionMaquinaActualizar(activa=False))
        assert (await svc.obtener(maquina.id)).estado == "OCUPADA"   # la otra la mantiene ocupada


async def test_patch_asignacion_de_otra_maquina_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        m1 = await svc.crear(_maquina(codigo="M-001"))
        m2 = await svc.crear(_maquina(codigo="M-002"))
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        asig = await svc.crear_asignacion(m1.id, AsignacionMaquinaCrear(obra_id=oid))
        with pytest.raises(AsignacionInexistente):
            await svc.actualizar_asignacion(
                m2.id, asig.id, AsignacionMaquinaActualizar(activa=False)
            )


# ---- Eventos SSE (espía de publish) -----------------------------------------------------------
async def test_evento_asignacion_maquina_actualizada_en_crear_y_editar(tenant, monkeypatch):
    eventos = _espia_publish(monkeypatch)
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        asig = await svc.crear_asignacion(maquina.id, AsignacionMaquinaCrear(obra_id=oid))
        await svc.actualizar_asignacion(
            maquina.id, asig.id, AsignacionMaquinaActualizar(activa=False)
        )

    nombres = [e for e, _ in eventos]
    assert nombres.count("asignacion_maquina_actualizada") == 2
    payload = eventos[0][1]
    assert payload["maquina_id"] == maquina.id
    assert payload["obra_id"] == oid
    assert "asignacion_id" in payload and "activa" in payload


async def test_evento_registro_horas_creado(tenant, monkeypatch):
    eventos = _espia_publish(monkeypatch)
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        await svc.crear_asignacion(
            maquina.id, AsignacionMaquinaCrear(obra_id=oid, fecha_inicio=today_co())
        )
        await svc.registrar_horas(
            maquina.id, RegistroHorasCrear(obra_id=oid, fecha=today_co(), horas_trabajadas=Decimal("8"))
        )

    nombres = [e for e, _ in eventos]
    assert "registro_horas_creado" in nombres
