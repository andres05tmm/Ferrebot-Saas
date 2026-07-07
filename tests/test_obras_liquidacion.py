"""Liquidación de obra: snapshot INMUTABLE + idempotencia + exige FINALIZADA.

Al liquidar una obra FINALIZADA se congela el gasto real definitivo en `liquidaciones_obra` (los 5
componentes + total + presupuesto + utilidad real + semáforo + snapshot_json) y la obra pasa a LIQUIDADA.
La operación es IDEMPOTENTE (UNIQUE obra_id): re-liquidar devuelve la MISMA fila, sin recalcular ni crear
otra — ni siquiera si aparecen gastos nuevos después (el número liquidado es histórico). Se corre contra
Postgres efímero (fixture `tenant`).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.obra.errors import ObraNoFinalizada
from modules.obra.repository import SqlObrasRepository
from modules.obra.service import ObrasService


def _servicio(s: AsyncSession) -> ObrasService:
    return ObrasService(SqlObrasRepository(s))


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
        )
    ).scalar_one()


async def _obra_con_cotizacion(s: AsyncSession, cid: int, *, estado: str) -> int:
    """Obra ligada a una cotización GANADA (subtotal 10M → ingreso 11.2M, U 400k), en el estado dado."""
    numero = f"PIM-{uuid.uuid4().hex[:8]}-2026"
    cot_id = (
        await s.execute(
            text(
                "INSERT INTO cotizaciones_obra "
                "(numero, cliente_id, nombre_obra, administracion_pct, imprevistos_pct, utilidad_pct, "
                " iva_sobre_utilidad_pct, estado) "
                "VALUES (:num,:c,'Vía',0.05,0.03,0.04,0.19,'GANADA') RETURNING id"
            ),
            {"num": numero, "c": cid},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO items_cotizacion_obra "
            "(cotizacion_id, orden, descripcion, unidad, cantidad, valor_unitario) "
            "VALUES (:c,1,'renglón','m3',1000,10000)"
        ),
        {"c": cot_id},
    )
    return (
        await s.execute(
            text(
                "INSERT INTO obras (cotizacion_id, cliente_id, nombre, estado) "
                "VALUES (:cot,:c,'Obra',:e) RETURNING id"
            ),
            {"cot": cot_id, "c": cid, "e": estado},
        )
    ).scalar_one()


async def _gasto(s: AsyncSession, oid: int, monto: str) -> None:
    await s.execute(
        text("INSERT INTO gastos (categoria, monto, obra_id) VALUES ('otros', :m, :o)"),
        {"m": monto, "o": oid},
    )


async def _estado_obra(engine, oid: int) -> str:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT estado FROM obras WHERE id=:o"), {"o": oid})
        ).scalar_one()


async def _cuenta_liquidaciones(engine, oid: int) -> int:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT count(*) FROM liquidaciones_obra WHERE obra_id=:o"), {"o": oid}
            )
        ).scalar_one()


async def test_liquidar_congela_snapshot_y_transiciona(tenant):
    """Liquidar una obra FINALIZADA escribe el snapshot y la deja LIQUIDADA."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, estado="FINALIZADA")
        await _gasto(s, oid, "1000000")   # gasto real = 1.000.000
        await s.commit()

        liq = await _servicio(s).liquidar(oid)
        await s.commit()
        liq_id = liq.id

    assert liq.obra_id == oid
    assert liq.ingreso_presupuestado == Decimal("11200000.0000")
    assert liq.utilidad_presupuestada == Decimal("400000.0000")
    assert liq.gasto_total == Decimal("1000000.0000")
    assert liq.total_gastos == Decimal("1000000.0000")
    assert liq.total_compras == Decimal("0.0000")
    assert liq.utilidad_real == Decimal("10200000.0000")   # 11.2M − 1.0M
    assert liq.semaforo == "verde"
    assert liq.snapshot_json["gasto_total"] == "1000000.00"
    assert liq.snapshot_json["semaforo"] == "verde"

    assert await _estado_obra(tenant.engine, oid) == "LIQUIDADA"
    assert await _cuenta_liquidaciones(tenant.engine, oid) == 1

    # relee la liquidación en otra sesión (persistió como snapshot inmutable)
    async with AsyncSession(tenant.engine) as s:
        got = await _servicio(s).obtener_liquidacion(oid)
        assert got.id == liq_id and got.gasto_total == Decimal("1000000.0000")


async def test_reliquidar_es_idempotente_y_no_recalcula(tenant):
    """Re-liquidar devuelve la MISMA fila; un gasto agregado después NO cambia el número congelado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, estado="FINALIZADA")
        await _gasto(s, oid, "1000000")
        await s.commit()
        liq1 = await _servicio(s).liquidar(oid)
        await s.commit()
        id1, total1 = liq1.id, liq1.gasto_total

    # aparece un gasto NUEVO tras la liquidación (el gasto real "vivo" sería 6M)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _gasto(s, oid, "5000000")
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        liq2 = await _servicio(s).liquidar(oid)   # segunda liquidación
        await s.commit()

    assert liq2.id == id1                              # misma fila, no una nueva
    assert liq2.gasto_total == total1 == Decimal("1000000.0000")   # congelado (no recalculó a 6M)
    assert await _cuenta_liquidaciones(tenant.engine, oid) == 1     # una sola liquidación
    assert await _estado_obra(tenant.engine, oid) == "LIQUIDADA"


async def test_liquidar_exige_obra_finalizada(tenant):
    """Una obra EN_EJECUCIÓN no se puede liquidar (409): sólo el cierre FINALIZADA→LIQUIDADA es válido."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, estado="EN_EJECUCION")
        await _gasto(s, oid, "1000000")
        await s.commit()

        with pytest.raises(ObraNoFinalizada):
            await _servicio(s).liquidar(oid)
        await s.rollback()

    assert await _cuenta_liquidaciones(tenant.engine, oid) == 0
    assert await _estado_obra(tenant.engine, oid) == "EN_EJECUCION"


async def test_liquidacion_aislada_entre_empresas(tenant_factory):
    """La liquidación de A no aparece en B (bases distintas)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid_a = await _obra_con_cotizacion(s, cid, estado="FINALIZADA")
        await _gasto(s, oid_a, "1000000")
        await s.commit()
        await _servicio(s).liquidar(oid_a)
        await s.commit()

    async with AsyncSession(empresa_b.engine) as s:
        total_b = (
            await s.execute(text("SELECT count(*) FROM liquidaciones_obra"))
        ).scalar_one()
    assert total_b == 0
