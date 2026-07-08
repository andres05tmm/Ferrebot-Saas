"""Registro de horas de máquina con mínimo facturable (Fase 3, plan PIM §5).

Ejercita el WRITE `MaquinariaService.registrar_horas` contra Postgres efímero (Docker 5433):
  - MÍNIMO facturable: `horas_facturables = max(horas_trabajadas, minimo)` de la asignación (3→min5=5; 6→6).
  - INGRESO calculado = horas_facturables × precio_hora PACTADO de la asignación (puede diferir del default).
  - IDEMPOTENCIA (invariante del carve-out, test-primero): reintentar el mismo parte
    (máquina, obra, fecha) NO crea dos registros — el bot de Fase 6 puede reintentar sin duplicar el
    cargo a cartera de Fase 5.
  - AISLAMIENTO multi-tenant: un registro en la empresa A jamás aparece en la B.

La asignación ACTIVA de (máquina, obra) aporta precio y mínimo; sin ella no se puede facturar (409).
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.maquinaria.errors import MaquinaInexistente, SinAsignacionActiva
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import MaquinaCrear, RegistroHorasCrear
from modules.maquinaria.service import MaquinariaService


def _service(session: AsyncSession) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(session))


async def _seed(
    session: AsyncSession, *, precio_hora: str = "160000", minimo_horas: int = 5, codigo: str = "M-001"
) -> tuple[int, int]:
    """Crea máquina (por service) + cliente/obra/asignación activa (por SQL). Devuelve (maquina_id, obra_id).

    La asignación arranca 2026-01-01 sin `fecha_fin` (vigente): cubre cualquier parte posterior. El
    precio/mínimo van POR ASIGNACIÓN (distintos del default de la máquina, para probar que se usa lo pactado).
    """
    svc = _service(session)
    maquina = await svc.crear(
        MaquinaCrear(
            codigo=codigo, nombre="Vibrocompactador", tipo="vibrocompactador",
            precio_hora_default=Decimal("150000"), minimo_horas_factura=1,
        )
    )
    cliente_id = (
        await session.execute(text("INSERT INTO clientes (nombre) VALUES ('Cli') RETURNING id"))
    ).scalar_one()
    obra_id = (
        await session.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Obra 1') RETURNING id"),
            {"c": cliente_id},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO asignaciones_maquina_obra "
            "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas, activa) "
            "VALUES (:m, :o, '2026-01-01', :p, :min, true)"
        ),
        {"m": maquina.id, "o": obra_id, "p": precio_hora, "min": minimo_horas},
    )
    await session.flush()
    return maquina.id, obra_id


async def _contar_registros(session: AsyncSession, maquina_id: int) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM registros_horas_maquina WHERE maquina_id = :m"),
            {"m": maquina_id},
        )
    ).scalar_one()


async def test_minimo_facturable_aplica_piso(tenant):
    """Trabajó MENOS que el mínimo → se factura el mínimo (piso de movilización/alistamiento)."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5, precio_hora="160000")
        r = await _service(s).registrar_horas(
            maquina_id,
            RegistroHorasCrear(obra_id=obra_id, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("3")),
        )
        assert r.horas_trabajadas == Decimal("3")
        assert r.horas_facturables == Decimal("5")          # 3 < mínimo 5 → se cobra 5
        assert r.minimo_cubierto is False
        assert r.precio_hora == Decimal("160000")           # precio PACTADO de la asignación, no el default
        assert r.ingreso == Decimal("5") * Decimal("160000")
        assert r.replay is False


async def test_horas_sobre_el_minimo_se_cobran_completas(tenant):
    """Trabajó MÁS que el mínimo → se factura lo trabajado."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5, precio_hora="160000")
        r = await _service(s).registrar_horas(
            maquina_id,
            RegistroHorasCrear(obra_id=obra_id, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("6")),
        )
        assert r.horas_facturables == Decimal("6")
        assert r.minimo_cubierto is True
        assert r.ingreso == Decimal("6") * Decimal("160000")


async def test_idempotencia_no_duplica_registro(tenant):
    """INVARIANTE (carve-out, test-primero): reintentar el mismo parte (máquina, obra, fecha) NO crea dos
    registros. Es lo que deja al bot de Fase 6 reintentar sin duplicar (y al cargo de cartera de Fase 5
    asentarse una sola vez)."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, obra_id = await _seed(s, minimo_horas=5, precio_hora="160000")
        datos = RegistroHorasCrear(
            obra_id=obra_id, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("6"),
            idempotency_key="bot:msg:42",
        )
        primero = await _service(s).registrar_horas(maquina_id, datos)
        segundo = await _service(s).registrar_horas(maquina_id, datos)

        assert primero.replay is False
        assert segundo.replay is True
        assert segundo.registro_id == primero.registro_id     # devuelve el MISMO registro
        assert segundo.horas_facturables == primero.horas_facturables
        assert await _contar_registros(s, maquina_id) == 1     # una sola fila en la BD


async def test_sin_asignacion_activa_falla_409(tenant):
    """Sin asignación activa que cubra la fecha no hay precio/mínimo pactados → SinAsignacionActiva (409)."""
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        maquina = await svc.crear(
            MaquinaCrear(
                codigo="M-9", nombre="Sin asignar", tipo="t", precio_hora_default=Decimal("1"),
            )
        )
        cliente_id = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('C') RETURNING id"))
        ).scalar_one()
        obra_id = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'O') RETURNING id"),
                {"c": cliente_id},
            )
        ).scalar_one()
        await s.flush()
        with pytest.raises(SinAsignacionActiva):
            await svc.registrar_horas(
                maquina.id,
                RegistroHorasCrear(obra_id=obra_id, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("4")),
            )


async def test_maquina_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(MaquinaInexistente):
            await _service(s).registrar_horas(
                999999,
                RegistroHorasCrear(obra_id=1, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("4")),
            )


async def test_aislamiento_registro_no_cruza_empresas(tenant_factory):
    """Invariante multi-tenant: un parte registrado en la empresa A jamás aparece en la B (la base ES la
    frontera; no hay `empresa_id`)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine) as sa:
        maquina_id, obra_id = await _seed(sa, minimo_horas=5)
        await _service(sa).registrar_horas(
            maquina_id,
            RegistroHorasCrear(obra_id=obra_id, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("6")),
        )
        await sa.commit()

    async with AsyncSession(empresa_b.engine) as sb:
        total_b = (
            await sb.execute(text("SELECT count(*) FROM registros_horas_maquina"))
        ).scalar_one()
        assert total_b == 0
