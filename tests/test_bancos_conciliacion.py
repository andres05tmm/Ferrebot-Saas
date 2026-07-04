"""Conciliación bancaria (ADR 0028) — invariantes críticos contra base efímera real.

Cubre (TDD de invariantes):
- Ingesta idempotente por `referencia_bancaria`: reprocesar el mismo extracto NO duplica.
- Conciliar NO altera saldos (ventas, CxP, caja): solo enlaza registros existentes.
- Montos ambiguos JAMÁS se auto-concilian (≥2 candidatos → queda no_conciliado).
- Aislamiento multi-tenant: la empresa B no ve movimientos de A.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.bancos.repository import SqlBancosRepository
from modules.bancos.schemas import MovimientoBancarioIngesta
from modules.bancos.service import BancosService

_DIA = date(2026, 6, 15)
# Mediodía UTC-5: `::date` cae en 2026-06-15 en cualquier TZ de sesión (asyncpg exige datetime, no str).
_TS = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5)))


def _svc(s: AsyncSession) -> BancosService:
    return BancosService(SqlBancosRepository(s))


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
    ).scalar_one()


async def _venta_transferencia(s: AsyncSession, *, uid: int, total: str, consecutivo: int) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                "metodo_pago, estado) VALUES (:c, :uid, :f, :t, 0, :t, 'transferencia', 'completada') "
                "RETURNING id"
            ),
            {"c": consecutivo, "uid": uid, "f": _TS, "t": total},
        )
    ).scalar_one()


async def _snapshot_saldos(s: AsyncSession) -> dict[str, Decimal]:
    """Suma de los libros que la conciliación NUNCA debe tocar."""
    async def _sum(q: str) -> Decimal:
        return Decimal((await s.execute(text(q))).scalar_one())
    return {
        "ventas": await _sum("SELECT COALESCE(SUM(total),0) FROM ventas"),
        "cxp_pendiente": await _sum("SELECT COALESCE(SUM(pendiente),0) FROM facturas_proveedores"),
        "gastos": await _sum("SELECT COALESCE(SUM(monto),0) FROM gastos"),
        "caja_mov": await _sum("SELECT COALESCE(SUM(monto),0) FROM caja_movimientos"),
    }


async def test_ingesta_idempotente_por_referencia(tenant):
    mov = MovimientoBancarioIngesta(
        referencia_bancaria="REF-001", fecha=_DIA, monto=Decimal("100000"), naturaleza="credito",
    )
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).ingestar([mov, mov])   # el mismo extracto trae la línea repetida
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).ingestar([mov])        # reprocesar el extracto completo otra vez
        await s.commit()

    assert r1.insertados == 1 and r1.duplicados == 1     # dentro de la misma corrida ya deduplica
    assert r2.insertados == 0 and r2.duplicados == 1     # reproceso: cero duplicados nuevos
    async with AsyncSession(tenant.engine) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM bancolombia_transferencias WHERE referencia_bancaria='REF-001'")
            )
        ).scalar_one()
    assert n == 1


async def test_conciliar_no_altera_saldos(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _venta_transferencia(s, uid=uid, total="250000", consecutivo=1)
        await s.commit()
        antes = await _snapshot_saldos(s)

        svc = _svc(s)
        await svc.ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-V1", fecha=_DIA, monto=Decimal("250000"), naturaleza="credito",
        )])
        await s.commit()
        assert await svc.sugerir_pendientes() == 1        # candidato único → sugerido
        await s.commit()

        pendientes = await svc.listar(estado="sugerido")
        mov_id = pendientes[0].movimiento.id
        cand = pendientes[0].candidatos[0]
        leer = await svc.confirmar(mov_id, tipo=cand.tipo, id_interno=cand.id, ahora=now_co())
        await s.commit()
        assert leer.estado_conciliacion == "conciliado"

        despues = await _snapshot_saldos(s)
    assert antes == despues, f"conciliar alteró saldos: {antes} != {despues}"


async def test_montos_ambiguos_nunca_se_autoconcilian(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        # DOS ventas idénticas en monto+fecha → ambigüedad.
        await _venta_transferencia(s, uid=uid, total="80000", consecutivo=1)
        await _venta_transferencia(s, uid=uid, total="80000", consecutivo=2)
        await s.commit()

        svc = _svc(s)
        await svc.ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-AMB", fecha=_DIA, monto=Decimal("80000"), naturaleza="credito",
        )])
        await s.commit()
        assert await svc.sugerir_pendientes() == 0        # regla dura: 2 candidatos → NO se toca
        await s.commit()

        pend = await svc.listar(estado=None)
    assert len(pend) == 1
    assert pend[0].movimiento.estado_conciliacion == "no_conciliado"
    assert len(pend[0].candidatos) == 2                   # ambos candidatos listados para resolver a mano


async def test_confirmar_enlace_invalido_no_concilia(tenant):
    from modules.bancos.errors import ConciliacionInvalida
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = _svc(s)
        await svc.ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="REF-X", fecha=_DIA, monto=Decimal("999"), naturaleza="credito",
        )])
        await s.commit()
        mov = (await svc.listar(estado=None))[0].movimiento
        with pytest.raises(ConciliacionInvalida):
            await svc.confirmar(mov.id, tipo="venta", id_interno=424242, ahora=now_co())
        await s.rollback()


async def test_aislamiento_a_no_ve_movimientos_de_b(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _svc(s).ingestar([MovimientoBancarioIngesta(
            referencia_bancaria="A-REF", fecha=_DIA, monto=Decimal("1000"), naturaleza="credito",
        )])
        await s.commit()
    async with AsyncSession(b.engine) as s:
        assert await _svc(s).listar(estado=None) == []
    async with AsyncSession(a.engine) as s:
        movs = await _svc(s).listar(estado=None)
    assert [m.movimiento.referencia_bancaria for m in movs] == ["A-REF"]
