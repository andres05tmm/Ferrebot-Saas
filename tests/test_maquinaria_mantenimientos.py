"""CRUD de mantenimientos de máquina (Fase 1 del cockpit de construcción) — doble capa.

(0) AISLAMIENTO multi-tenant (invariante crítico, TEST-PRIMERO): un mantenimiento dado de alta en la
    empresa A JAMÁS aparece al consultar la B (la base ES la frontera; no hay `empresa_id`).
(1) Wiring HTTP con servicio FAKE (patrón `test_obras_panel.py`): forma de la respuesta, 404 de máquina/
    mantenimiento inexistente, 403 del vendedor en las mutaciones y el gate de la capacidad `maquinaria`.
(2) Integración real contra Postgres efímero: alta (fecha default hoy Colombia), orden fecha DESC, edición
    parcial, DELETE duro, que crear NO cambia `maquina.estado`, y los agregados que consume la Fase 2
    (`ultimo_mantenimiento_por_maquina` DISTINCT ON + `horas_desde` batcheado).
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from modules.maquinaria.errors import MantenimientoInexistente, MaquinaInexistente
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.router import get_maquinaria_service, router
from modules.maquinaria.schemas import (
    MantenimientoActualizar,
    MantenimientoCrear,
    MaquinaCrear,
)
from modules.maquinaria.service import MaquinariaService


def _service(session: AsyncSession) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(session))


def _maquina(**over) -> MaquinaCrear:
    base = {
        "codigo": "M-001",
        "nombre": "Vibrocompactador CAT CS533E",
        "tipo": "vibrocompactador",
        "precio_hora_default": Decimal("150000"),
    }
    base.update(over)
    return MaquinaCrear(**base)


# =====================================================================================================
# (0) AISLAMIENTO multi-tenant — invariante crítico, escrito PRIMERO
# =====================================================================================================
async def _contar_mantenimientos(engine) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM mantenimientos"))).scalar_one()


async def test_empresa_A_no_ve_mantenimientos_de_empresa_B(tenant_factory):
    """Un mantenimiento asentado en la empresa A jamás aparece en la B (la frontera es la base)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine) as sa:
        maquina = await _service(sa).crear(_maquina(codigo="M-001"))
        await _service(sa).crear_mantenimiento(
            maquina.id,
            MantenimientoCrear(tipo="PREVENTIVO", descripcion="Cambio de aceite", costo=Decimal("200000")),
        )
        await sa.commit()

    assert await _contar_mantenimientos(empresa_a.engine) == 1   # A ve el suyo
    assert await _contar_mantenimientos(empresa_b.engine) == 0   # B no ve nada de A


# =====================================================================================================
# (1) Wiring HTTP con servicio FAKE
# =====================================================================================================
def _mant(**over) -> SimpleNamespace:
    base = dict(
        id=7, maquina_id=1, tipo="PREVENTIVO", fecha=date(2026, 7, 8), horas_maquina=Decimal("1200"),
        descripcion="Cambio de aceite y filtros", costo=Decimal("250000.00"), proveedor_id=None,
        proximo_en_horas=Decimal("500"), proximo_en_fecha=date(2026, 10, 8), factura_url=None,
        creado_en="2026-07-08T12:00:00-05:00",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeMaquinaria:
    """Fake del `MaquinariaService` para el wiring HTTP (sin BD): permite forzar los 404 de dominio."""

    def __init__(self, *, maquina_existe: bool = True, mantenimiento_existe: bool = True) -> None:
        self._maquina_existe = maquina_existe
        self._mantenimiento_existe = mantenimiento_existe

    async def listar_mantenimientos(self, maquina_id, *, limite=100, offset=0):
        if not self._maquina_existe:
            raise MaquinaInexistente(maquina_id)
        return [_mant()]

    async def crear_mantenimiento(self, maquina_id, datos):
        if not self._maquina_existe:
            raise MaquinaInexistente(maquina_id)
        return _mant(maquina_id=maquina_id, tipo=datos.tipo, descripcion=datos.descripcion)

    async def actualizar_mantenimiento(self, maquina_id, mantenimiento_id, datos):
        if not self._mantenimiento_existe:
            raise MantenimientoInexistente(mantenimiento_id)
        return _mant(id=mantenimiento_id, maquina_id=maquina_id)

    async def eliminar_mantenimiento(self, maquina_id, mantenimiento_id):
        if not self._mantenimiento_existe:
            raise MantenimientoInexistente(mantenimiento_id)


def _app(service, *, rol="admin", caps=frozenset({"maquinaria"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_maquinaria_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_listar_200_forma():
    async with _cliente(_app(_FakeMaquinaria())) as c:
        r = await c.get("/api/v1/maquinas/1/mantenimientos")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    fila = body[0]
    assert fila["tipo"] == "PREVENTIVO"
    assert fila["costo"] == "250000.00"          # Decimal serializa como string
    assert fila["proximo_en_fecha"] == "2026-10-08"


async def test_crear_201_forma():
    async with _cliente(_app(_FakeMaquinaria())) as c:
        r = await c.post(
            "/api/v1/maquinas/3/mantenimientos",
            json={"tipo": "CORRECTIVO", "descripcion": "Reparación de oruga", "costo": "1800000"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["maquina_id"] == 3
    assert body["tipo"] == "CORRECTIVO"
    assert body["descripcion"] == "Reparación de oruga"


async def test_crear_403_vendedor():
    """Mutación financiera/operativa: el vendedor no da de alta mantenimientos (403)."""
    async with _cliente(_app(_FakeMaquinaria(), rol="vendedor")) as c:
        r = await c.post(
            "/api/v1/maquinas/1/mantenimientos",
            json={"tipo": "PREVENTIVO", "descripcion": "x"},
        )
    assert r.status_code == 403, r.text


async def test_patch_y_delete_403_vendedor():
    async with _cliente(_app(_FakeMaquinaria(), rol="vendedor")) as c:
        rp = await c.patch("/api/v1/maquinas/1/mantenimientos/7", json={"costo": "1"})
        rd = await c.delete("/api/v1/maquinas/1/mantenimientos/7")
    assert rp.status_code == 403, rp.text
    assert rd.status_code == 403, rd.text


async def test_listar_lo_puede_ver_vendedor():
    """Las lecturas sí son de rol vendedor (personal de campo consulta la bitácora)."""
    async with _cliente(_app(_FakeMaquinaria(), rol="vendedor")) as c:
        r = await c.get("/api/v1/maquinas/1/mantenimientos")
    assert r.status_code == 200, r.text


async def test_delete_204_admin():
    async with _cliente(_app(_FakeMaquinaria())) as c:
        r = await c.delete("/api/v1/maquinas/1/mantenimientos/7")
    assert r.status_code == 204, r.text


async def test_gateado_por_maquinaria():
    async with _cliente(_app(_FakeMaquinaria(), caps=frozenset())) as c:
        r = await c.get("/api/v1/maquinas/1/mantenimientos")
    assert r.status_code == 404, r.text


async def test_404_maquina_inexistente_al_crear():
    async with _cliente(_app(_FakeMaquinaria(maquina_existe=False))) as c:
        r = await c.post(
            "/api/v1/maquinas/999/mantenimientos",
            json={"tipo": "PREVENTIVO", "descripcion": "x"},
        )
    assert r.status_code == 404, r.text


async def test_404_mantenimiento_inexistente_al_editar_o_borrar():
    async with _cliente(_app(_FakeMaquinaria(mantenimiento_existe=False))) as c:
        rp = await c.patch("/api/v1/maquinas/1/mantenimientos/999", json={"costo": "1"})
        rd = await c.delete("/api/v1/maquinas/1/mantenimientos/999")
    assert rp.status_code == 404, rp.text
    assert rd.status_code == 404, rd.text


# =====================================================================================================
# (2) Integración real (Postgres efímero)
# =====================================================================================================
async def test_crear_fecha_default_hoy_y_no_cambia_estado(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina(estado="DISPONIBLE"))

        mant = await svc.crear_mantenimiento(
            maquina.id,
            MantenimientoCrear(tipo="INSPECCION", descripcion="Revisión general"),
        )
        assert mant.fecha == today_co()          # default hoy Colombia (regla #4)
        assert mant.costo == Decimal("0")        # default costo 0
        assert mant.id is not None

        # Registrar un mantenimiento NO saca la máquina de operación.
        recargada = await svc.obtener(maquina.id)
        assert recargada.estado == "DISPONIBLE"


async def test_listar_orden_fecha_desc(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        await svc.crear_mantenimiento(
            maquina.id, MantenimientoCrear(tipo="PREVENTIVO", descripcion="viejo", fecha=date(2026, 1, 1))
        )
        await svc.crear_mantenimiento(
            maquina.id, MantenimientoCrear(tipo="CORRECTIVO", descripcion="nuevo", fecha=date(2026, 6, 1))
        )
        lista = await svc.listar_mantenimientos(maquina.id)
        assert [m.descripcion for m in lista] == ["nuevo", "viejo"]   # fecha DESC


async def test_listar_maquina_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(MaquinaInexistente):
            await _service(s).listar_mantenimientos(999999)


async def test_actualizar_parcial_solo_toca_lo_enviado(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        mant = await svc.crear_mantenimiento(
            maquina.id,
            MantenimientoCrear(tipo="PREVENTIVO", descripcion="orig", costo=Decimal("100000")),
        )
        actualizado = await svc.actualizar_mantenimiento(
            maquina.id, mant.id, MantenimientoActualizar(costo=Decimal("175000"))
        )
        assert actualizado.costo == Decimal("175000")
        assert actualizado.descripcion == "orig"        # no se tocó
        assert actualizado.tipo == "PREVENTIVO"


async def test_actualizar_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        maquina = await _service(s).crear(_maquina())
        with pytest.raises(MantenimientoInexistente):
            await _service(s).actualizar_mantenimiento(
                maquina.id, 999999, MantenimientoActualizar(costo=Decimal("1"))
            )


async def test_eliminar_delete_duro_saca_de_lista_y_404_al_reborrar(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_maquina())
        mant = await svc.crear_mantenimiento(
            maquina.id, MantenimientoCrear(tipo="PREVENTIVO", descripcion="a borrar")
        )
        await svc.eliminar_mantenimiento(maquina.id, mant.id)
        assert await svc.listar_mantenimientos(maquina.id) == []   # DELETE duro: se fue de verdad
        with pytest.raises(MantenimientoInexistente):
            await svc.eliminar_mantenimiento(maquina.id, mant.id)


async def test_mantenimiento_acotado_por_maquina(tenant):
    """Un mantenimiento de otra máquina es inexistente para la ruta de esta máquina (no se cruza)."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        m1 = await svc.crear(_maquina(codigo="M-001"))
        m2 = await svc.crear(_maquina(codigo="M-002"))
        mant = await svc.crear_mantenimiento(
            m1.id, MantenimientoCrear(tipo="PREVENTIVO", descripcion="de la 1")
        )
        with pytest.raises(MantenimientoInexistente):
            await svc.actualizar_mantenimiento(
                m2.id, mant.id, MantenimientoActualizar(costo=Decimal("1"))
            )


# ---- Agregados que consume la Fase 2 -----------------------------------------------------------
async def test_ultimo_mantenimiento_por_maquina_distinct_on(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        repo = SqlMaquinasRepository(s)
        m1 = await svc.crear(_maquina(codigo="M-001"))
        m2 = await svc.crear(_maquina(codigo="M-002"))
        await svc.crear_mantenimiento(
            m1.id, MantenimientoCrear(tipo="PREVENTIVO", descripcion="m1 viejo", fecha=date(2026, 1, 1))
        )
        ultimo_m1 = await svc.crear_mantenimiento(
            m1.id, MantenimientoCrear(tipo="CORRECTIVO", descripcion="m1 nuevo", fecha=date(2026, 5, 1))
        )
        ultimo_m2 = await svc.crear_mantenimiento(
            m2.id, MantenimientoCrear(tipo="INSPECCION", descripcion="m2 único", fecha=date(2026, 3, 1))
        )
        por_maquina = await repo.ultimo_mantenimiento_por_maquina()
        assert por_maquina[m1.id].id == ultimo_m1.id     # el más reciente de m1
        assert por_maquina[m2.id].id == ultimo_m2.id
        assert set(por_maquina) == {m1.id, m2.id}


async def test_horas_desde_batch_suma_solo_posteriores_al_corte(tenant):
    """`horas_desde` suma las horas facturables de los partes POSTERIORES (estricto) al corte, batcheado."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        repo = SqlMaquinasRepository(s)
        m1 = await svc.crear(_maquina(codigo="M-001"))
        m2 = await svc.crear(_maquina(codigo="M-002"))
        cliente_id = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Cli') RETURNING id"))
        ).scalar_one()
        obra_id = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Obra') RETURNING id"),
                {"c": cliente_id},
            )
        ).scalar_one()

        async def _reg(maquina_id, fecha, horas):
            await s.execute(
                text(
                    "INSERT INTO registros_horas_maquina "
                    "(maquina_id, obra_id, fecha, horas_trabajadas, horas_facturables) "
                    "VALUES (:m, :o, :f, :h, :h)"
                ),
                {"m": maquina_id, "o": obra_id, "f": fecha, "h": horas},
            )

        # m1: corte 2026-03-01. El del día del corte NO cuenta (estricto); los dos posteriores sí (8+5=13).
        await _reg(m1.id, date(2026, 3, 1), Decimal("4"))
        await _reg(m1.id, date(2026, 3, 5), Decimal("8"))
        await _reg(m1.id, date(2026, 3, 9), Decimal("5"))
        # m2: sin partes tras su corte → 0 (LEFT JOIN).
        await s.flush()

        horas = await repo.horas_desde([(m1.id, date(2026, 3, 1)), (m2.id, date(2026, 1, 1))])
        assert horas[m1.id] == Decimal("13")
        assert horas[m2.id] == Decimal("0")

    async with AsyncSession(tenant.engine) as s2:
        assert await SqlMaquinasRepository(s2).horas_desde([]) == {}   # sin pares no consulta
