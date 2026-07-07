"""Integración del repositorio de obras contra Postgres efímero + aislamiento multi-tenant.

Cubre el CRUD real (persistencia, filtro por estado, transición, soft delete que oculta pero no borra),
los conteos de operación y los reportes diarios. El último test es el INVARIANTE CRÍTICO de
multi-tenancy (.claude/rules/multitenancy.md): una obra dada de alta en la empresa A JAMÁS aparece al
consultar la B, porque cada empresa es una base distinta (la frontera la da la base, no una columna).
"""
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.obra.repository import SqlObrasRepository
from modules.obra.schemas import ObraCrear, ReporteDiarioCrear


async def _crear_cliente(session: AsyncSession, nombre: str = "Alcaldía") -> int:
    """Siembra un cliente mínimo (la obra referencia `clientes.id` por FK)."""
    return (
        await session.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES (:n, 0) RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


async def _contar_obras(engine) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM obras"))).scalar_one()


async def test_crud_soft_delete_y_transicion(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlObrasRepository(s)
        cid = await _crear_cliente(s)
        planif = await repo.crear(ObraCrear(cliente_id=cid, nombre="Vía La Paz"))
        otra = await repo.crear(ObraCrear(cliente_id=cid, nombre="Puente Río"))
        await s.commit()
        pid, oid2 = planif.id, otra.id
        assert planif.estado == "PLANIFICADA"          # default de la base
        assert planif.cotizacion_id is None            # obra suelta (conversión = Fase 2)

    # obtener + listar (ambas visibles) y transición que persiste
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlObrasRepository(s)
        assert (await repo.obtener(pid)).nombre == "Vía La Paz"
        assert {o.id for o in await repo.listar()} == {pid, oid2}
        assert {o.id for o in await repo.listar(cliente_id=cid)} == {pid, oid2}
        await repo.cambiar_estado(await repo.obtener(pid), "EN_EJECUCION")
        await s.commit()

    # el filtro por estado ve el cambio; soft delete oculta la otra
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlObrasRepository(s)
        assert [o.id for o in await repo.listar(estado="EN_EJECUCION")] == [pid]
        assert await repo.obtener(pid) is not None and (await repo.obtener(pid)).estado == "EN_EJECUCION"
        await repo.soft_delete(await repo.obtener(oid2))
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        repo = SqlObrasRepository(s)
        assert await repo.obtener(oid2) is None                 # borrada: ya no visible
        assert [o.id for o in await repo.listar()] == [pid]     # excluida del listado
        assert await _contar_obras(tenant.engine) == 2          # soft, no hard: la fila sigue


async def test_conteos_y_reportes_diarios(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlObrasRepository(s)
        cid = await _crear_cliente(s)
        obra = await repo.crear(ObraCrear(cliente_id=cid, nombre="Vía"))
        await s.commit()
        oid = obra.id
        await repo.crear_reporte(
            oid, ReporteDiarioCrear(fecha=date(2026, 7, 1), avance_descripcion="Base granular")
        )
        await repo.crear_reporte(
            oid, ReporteDiarioCrear(fecha=date(2026, 7, 2), avance_descripcion="Imprimación")
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        repo = SqlObrasRepository(s)
        conteos = await repo.contar_operacion(oid)
        # sin máquinas/trabajadores asignados aún; dos reportes cargados
        assert (conteos.maquinas_asignadas, conteos.trabajadores_asignados) == (0, 0)
        assert conteos.reportes_diarios == 2
        reportes = await repo.listar_reportes(oid)
        assert [r.fecha for r in reportes] == [date(2026, 7, 2), date(2026, 7, 1)]  # recientes primero
        assert reportes[0].origen_registro == "MANUAL"


async def test_empresa_A_no_ve_obras_de_empresa_B(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine) as s:
        cid = await _crear_cliente(s, nombre="Cliente A")
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Obra solo de A')"),
            {"c": cid},
        )
        await s.commit()

    assert await _contar_obras(empresa_a.engine) == 1   # A ve su obra
    assert await _contar_obras(empresa_b.engine) == 0   # B no ve nada de A
