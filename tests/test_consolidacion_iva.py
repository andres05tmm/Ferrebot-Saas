"""Consolidación de IVA por bimestre (ADR 0027) contra Postgres efímero + aritmética pura del período.

Invariante crítico (TDD): reprocesar el mismo período NO duplica renglones de `libro_iva` ni filas de
`iva_saldos_bimestrales` (UPSERT idempotente). Cubre también: excluye ventas anuladas, materializa el
saldo, y aislamiento multi-tenant.
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ

# Instantes fijos dentro del bimestre 2/2026 (mar-abr), hora Colombia.
_FECHA_VENTA = datetime(2026, 3, 15, 12, 0, tzinfo=COLOMBIA_TZ)
_FECHA_COMPRA = datetime(2026, 3, 20, 10, 0, tzinfo=COLOMBIA_TZ)

from modules.reportes.consolidacion import (
    BimestreInvalido,
    ConsolidacionIVAService,
    SqlConsolidacionRepository,
    rango_bimestre,
)


# ── Aritmética pura del período ───────────────────────────────────────────────
def test_rango_bimestre_mapea_los_seis_periodos():
    from datetime import date

    assert rango_bimestre(2026, 1) == (date(2026, 1, 1), date(2026, 2, 28))
    assert rango_bimestre(2024, 1) == (date(2024, 1, 1), date(2024, 2, 29))   # bisiesto
    assert rango_bimestre(2026, 4) == (date(2026, 7, 1), date(2026, 8, 31))
    assert rango_bimestre(2026, 6) == (date(2026, 11, 1), date(2026, 12, 31))


def test_bimestre_fuera_de_rango_lanza():
    with pytest.raises(BimestreInvalido):
        rango_bimestre(2026, 0)
    with pytest.raises(BimestreInvalido):
        rango_bimestre(2026, 7)


# ── Integración ───────────────────────────────────────────────────────────────
async def _venta(s, *, consecutivo, subtotal, impuestos, total, estado="completada", fecha=_FECHA_VENTA):
    uid = (await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('A','vendedor') RETURNING id"))).scalar_one()
    await s.execute(
        text(
            "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago, estado, origen) "
            "VALUES (:c,:v,:f,:s,:i,:t,'efectivo',:e,'web')"
        ),
        {"c": consecutivo, "v": uid, "f": fecha, "s": subtotal, "i": impuestos, "t": total, "e": estado},
    )


async def _compra_fiscal(s, *, base, iva, total, creado=_FECHA_COMPRA):
    await s.execute(
        text("INSERT INTO compras_fiscal (proveedor_nit, base, iva, total, creado_en) VALUES ('900',:b,:i,:t,:c)"),
        {"b": base, "i": iva, "t": total, "c": creado},
    )


async def _sembrar(s):
    await _venta(s, consecutivo=1, subtotal="100000", impuestos="19000", total="119000")
    await _venta(s, consecutivo=2, subtotal="50000", impuestos="9500", total="59500")
    await _venta(s, consecutivo=3, subtotal="80000", impuestos="15200", total="95200", estado="anulada")
    await _compra_fiscal(s, base="40000", iva="7600", total="47600")
    await s.commit()


async def test_consolida_saldo_y_libro_excluyendo_anuladas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar(s)
        saldo = await ConsolidacionIVAService(SqlConsolidacionRepository(s)).consolidar_bimestre(anio=2026, bimestre=2)

    assert saldo.iva_generado == Decimal("28500.00")     # 19000 + 9500 (anulada fuera)
    assert saldo.iva_descontable == Decimal("7600.00")
    assert saldo.saldo == Decimal("20900.00")            # 28500 − 7600 (a pagar)

    async with AsyncSession(tenant.engine) as s:
        n_libro = (await s.execute(text("SELECT count(*) FROM libro_iva WHERE referencia IS NOT NULL"))).scalar_one()
        n_saldos = (await s.execute(text("SELECT count(*) FROM iva_saldos_bimestrales"))).scalar_one()
    assert n_libro == 3          # 2 ventas 'generado' + 1 compra 'descontable' (anulada NO entra)
    assert n_saldos == 1


async def test_reprocesar_es_idempotente_no_duplica(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar(s)
        svc = ConsolidacionIVAService(SqlConsolidacionRepository(s))
        await svc.consolidar_bimestre(anio=2026, bimestre=2)
        await svc.consolidar_bimestre(anio=2026, bimestre=2)   # reproceso
        saldo = await svc.consolidar_bimestre(anio=2026, bimestre=2)

    async with AsyncSession(tenant.engine) as s:
        n_libro = (await s.execute(text("SELECT count(*) FROM libro_iva WHERE referencia IS NOT NULL"))).scalar_one()
        n_saldos = (await s.execute(text("SELECT count(*) FROM iva_saldos_bimestrales"))).scalar_one()
    assert n_libro == 3                                  # sin duplicar tras 3 corridas
    assert n_saldos == 1
    assert saldo.saldo == Decimal("20900.00")            # el saldo se mantiene estable


async def test_listar_saldos_por_anio(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar(s)
        svc = ConsolidacionIVAService(SqlConsolidacionRepository(s))
        await svc.consolidar_bimestre(anio=2026, bimestre=2)
        saldos = await svc.listar_saldos(anio=2026)
        vacio = await svc.listar_saldos(anio=2099)
    assert len(saldos) == 1 and saldos[0].bimestre == 2
    assert vacio == []


async def test_aislamiento_saldos_entre_tenants(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _sembrar(s)
        await ConsolidacionIVAService(SqlConsolidacionRepository(s)).consolidar_bimestre(anio=2026, bimestre=2)
    async with AsyncSession(b.engine) as s:
        saldos_b = await ConsolidacionIVAService(SqlConsolidacionRepository(s)).listar_saldos(anio=None)
        n_libro_b = (await s.execute(text("SELECT count(*) FROM libro_iva"))).scalar_one()
    assert saldos_b == []
    assert n_libro_b == 0
