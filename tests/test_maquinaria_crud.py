"""CRUD de maquinaria por capa service/repo contra base efímera real (patrón test_inventario_crud).

Ejercita `MaquinariaService` sobre `SqlMaquinasRepository` en la sesión de un tenant efímero (Postgres
Docker migrado a head): alta, código duplicado (409), filtro por estado, edición PARCIAL (PATCH — solo
toca lo enviado), soft delete (`eliminado_en`: sale de la lista, 404 al obtener, el código sigue
ocupado) y las lecturas de operación (asignaciones/horas). El aislamiento entre empresas se prueba en
`test_aislamiento_maquinas.py` (invariante crítico).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.maquinaria.errors import CodigoMaquinaDuplicado, MaquinaInexistente
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import MaquinaActualizar, MaquinaCrear
from modules.maquinaria.service import MaquinariaService


def _service(session: AsyncSession) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(session))


def _payload(**over) -> MaquinaCrear:
    base = {
        "codigo": "M-001",
        "nombre": "Vibrocompactador CAT CS533E",
        "tipo": "vibrocompactador",
        "precio_hora_default": Decimal("150000"),
    }
    base.update(over)
    return MaquinaCrear(**base)


async def test_crear_y_obtener(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        creada = await svc.crear(_payload(minimo_horas_factura=4, notas="nueva"))
        assert creada.id is not None
        assert creada.estado == "DISPONIBLE"          # default de la spec
        assert creada.eliminado_en is None
        assert creada.precio_hora_default == Decimal("150000")

        obtenida = await svc.obtener(creada.id)
        assert obtenida.id == creada.id
        assert obtenida.minimo_horas_factura == 4
        assert obtenida.notas == "nueva"


async def test_crear_codigo_duplicado_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        await svc.crear(_payload(codigo="M-777"))
        with pytest.raises(CodigoMaquinaDuplicado):
            await svc.crear(_payload(codigo="M-777", nombre="Otra"))


async def test_listar_filtra_por_estado(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        await svc.crear(_payload(codigo="M-001", estado="DISPONIBLE"))
        await svc.crear(_payload(codigo="M-002", estado="MANTENIMIENTO"))
        await svc.crear(_payload(codigo="M-003", estado="MANTENIMIENTO"))

        todas = await svc.listar()
        assert {m.codigo for m in todas} == {"M-001", "M-002", "M-003"}

        en_mtto = await svc.listar(estado="MANTENIMIENTO")
        assert {m.codigo for m in en_mtto} == {"M-002", "M-003"}

        por_texto = await svc.listar(q="M-001")
        assert [m.codigo for m in por_texto] == ["M-001"]


async def test_actualizar_parcial_solo_toca_lo_enviado(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        creada = await svc.crear(_payload(estado="DISPONIBLE", notas="orig", minimo_horas_factura=2))

        # PATCH solo de estado: notas y precio y mínimo quedan intactos.
        actualizada = await svc.actualizar(creada.id, MaquinaActualizar(estado="MANTENIMIENTO"))
        assert actualizada.estado == "MANTENIMIENTO"
        assert actualizada.notas == "orig"
        assert actualizada.minimo_horas_factura == 2
        assert actualizada.precio_hora_default == Decimal("150000")


async def test_actualizar_codigo_duplicado_y_mismo_codigo_ok(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        m1 = await svc.crear(_payload(codigo="M-001"))
        m2 = await svc.crear(_payload(codigo="M-002"))

        # Reasignar el código de otra máquina → 409.
        with pytest.raises(CodigoMaquinaDuplicado):
            await svc.actualizar(m2.id, MaquinaActualizar(codigo="M-001"))

        # Enviar el MISMO código de la propia máquina no colisiona (se excluye a sí misma).
        misma = await svc.actualizar(m1.id, MaquinaActualizar(codigo="M-001", nombre="Renombrada"))
        assert misma.codigo == "M-001"
        assert misma.nombre == "Renombrada"


async def test_actualizar_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(MaquinaInexistente):
            await _service(s).actualizar(999999, MaquinaActualizar(nombre="X"))


async def test_soft_delete_saca_de_lista_y_404_al_obtener(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        creada = await svc.crear(_payload(codigo="M-001"))

        await svc.eliminar(creada.id)

        assert await svc.listar() == []                      # no aparece en la lista viva
        with pytest.raises(MaquinaInexistente):
            await svc.obtener(creada.id)                      # una eliminada es "inexistente"

        # El código sigue ocupado (el UNIQUE de la BD incluye las soft-deleted) → re-alta = 409.
        with pytest.raises(CodigoMaquinaDuplicado):
            await svc.crear(_payload(codigo="M-001"))


async def test_soft_delete_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(MaquinaInexistente):
            await _service(s).eliminar(999999)


async def test_lecturas_de_operacion_asignaciones_y_horas(tenant):
    """Las lecturas triviales de operación (asignaciones/horas) devuelven lo asociado a la máquina.

    Se siembran cliente → obra → asignación → parte de horas por SQL directo (el alta de operación es de
    Fase 3): aquí solo se valida el camino de LECTURA que expone el router.
    """
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(_payload(codigo="M-001"))

        cliente_id = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Cli') RETURNING id"))
        ).scalar_one()
        obra_id = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Obra 1') RETURNING id"),
                {"c": cliente_id},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO asignaciones_maquina_obra "
                "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas) "
                "VALUES (:m, :o, '2026-01-01', 160000, 4)"
            ),
            {"m": maquina.id, "o": obra_id},
        )
        await s.execute(
            text(
                "INSERT INTO registros_horas_maquina "
                "(maquina_id, obra_id, fecha, horas_trabajadas, horas_facturables) "
                "VALUES (:m, :o, '2026-01-02', 6, 6)"
            ),
            {"m": maquina.id, "o": obra_id},
        )
        await s.flush()

        asignaciones = await svc.listar_asignaciones(maquina.id)
        assert len(asignaciones) == 1
        assert asignaciones[0].obra_id == obra_id
        assert asignaciones[0].precio_hora == Decimal("160000")
        assert asignaciones[0].activa is True

        horas = await svc.listar_horas(maquina.id)
        assert len(horas) == 1
        assert horas[0].horas_facturables == Decimal("6")
        assert horas[0].origen_registro == "MANUAL"      # default de la spec

        # Una máquina sin operación devuelve listas vacías (el camino de lectura no revienta).
        otra = await svc.crear(_payload(codigo="M-002"))
        assert await svc.listar_asignaciones(otra.id) == []
        assert await svc.listar_horas(otra.id) == []
