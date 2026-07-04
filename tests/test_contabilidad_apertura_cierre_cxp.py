"""Cabos del motor contable (ADR 0030): asiento de apertura, cierre de período y CxP de factura
suelta. Invariantes TDD test-primero contra base efímera real.

Invariantes cubiertos:
- débitos = créditos en apertura, cierre y proyección de factura.
- inmutabilidad: apertura y cierre posteados no se editan (corregir = espejo).
- idempotencia: apertura/cierre/proyección reintentadas no duplican.
- aislamiento multi-tenant.
- el período cerrado NO admite más asientos (guard existente).
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.contabilidad.apertura import AperturaService
from modules.contabilidad.cierre import CierreService
from modules.contabilidad.errors import (
    AsientoInmutable,
    CorteVacio,
    PeriodoBloqueado,
    PeriodoInexistente,
)
from modules.contabilidad.fuente_repository import FuenteContableRepository
from modules.contabilidad.ledger import LedgerService
from modules.contabilidad.proyector import Proyector
from modules.contabilidad.repository import SqlContabilidadRepository
from modules.contabilidad.schemas import AsientoCrear, CorteApertura, LineaAsiento


# --- helpers -----------------------------------------------------------------
def _repo(s: AsyncSession) -> SqlContabilidadRepository:
    return SqlContabilidadRepository(s)


def _ledger(s: AsyncSession) -> LedgerService:
    return LedgerService(_repo(s))


async def _apertura(s: AsyncSession) -> AperturaService:
    repo = _repo(s)
    await repo.asegurar_puc()
    return AperturaService(LedgerService(repo), repo)


async def _cierre(s: AsyncSession) -> CierreService:
    repo = _repo(s)
    await repo.asegurar_puc()
    return CierreService(LedgerService(repo), repo)


async def _proyector(s: AsyncSession) -> Proyector:
    repo = _repo(s)
    await repo.asegurar_puc()
    return Proyector(LedgerService(repo), FuenteContableRepository(s))


def _linea(codigo, direction, amount) -> LineaAsiento:
    return LineaAsiento(cuenta_codigo=codigo, direction=direction, amount=Decimal(amount))


async def _lineas_de(engine, entry_id: int):
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text(
                    "SELECT p.codigo, l.direction, l.amount FROM journal_line l "
                    "JOIN puc_cuentas p ON p.id=l.cuenta_id WHERE l.entry_id=:e ORDER BY l.orden"
                ),
                {"e": entry_id},
            )
        ).all()


async def _n_asientos(engine, origen=None) -> int:
    async with AsyncSession(engine) as s:
        q = "SELECT count(*) FROM journal_entry"
        if origen:
            q += f" WHERE origen_tipo='{origen}'"
        return (await s.execute(text(q))).scalar_one()


def _cuadra(lineas) -> bool:
    deb = sum((a for _, d, a in lineas if d == "debit"), Decimal("0"))
    cred = sum((a for _, d, a in lineas if d == "credit"), Decimal("0"))
    return deb == cred


async def _factura(s: AsyncSession, fid: str, total: str, *, pendiente=None) -> str:
    await s.execute(
        text(
            "INSERT INTO facturas_proveedores (id, proveedor, total, pendiente, estado, fecha) "
            "VALUES (:id,'Proveedor SA',:t,:p,'pendiente',CURRENT_DATE)"
        ),
        {"id": fid, "t": total, "p": pendiente if pendiente is not None else total},
    )
    return fid


# --- APERTURA ----------------------------------------------------------------
async def test_apertura_balanceada_contra_patrimonio(tenant):
    """El asiento de apertura cuadra: activos al débito, CxP al crédito, patrimonio de cierre."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        corte = CorteApertura(
            fecha=now_co(), caja=Decimal("100000"), cartera=Decimal("50000"),
            inventario=Decimal("300000"), cuentas_por_pagar=Decimal("120000"),
        )
        res = await (await _apertura(s)).registrar_apertura(corte)
        eid = res.entry.id
        await s.commit()

    lineas = await _lineas_de(tenant.engine, eid)
    assert _cuadra(lineas)
    por_cuenta = {c: (d, a) for c, d, a in lineas}
    # Activos al débito, proveedores al crédito.
    assert por_cuenta["110505"] == ("debit", Decimal("100000.00"))
    assert por_cuenta["130505"] == ("debit", Decimal("50000.00"))
    assert por_cuenta["143501"] == ("debit", Decimal("300000.00"))
    assert por_cuenta["220505"] == ("credit", Decimal("120000.00"))
    # Patrimonio = activos (450000) − pasivos (120000) = 330000 al crédito.
    assert por_cuenta["310505"] == ("credit", Decimal("330000.00"))


async def test_apertura_patrimonio_negativo_va_al_debito(tenant):
    """Si los pasivos superan los activos, el patrimonio de cierre queda al débito."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        corte = CorteApertura(
            fecha=now_co(), caja=Decimal("10000"), cuentas_por_pagar=Decimal("80000"),
        )
        res = await (await _apertura(s)).registrar_apertura(corte)
        await s.commit()
    lineas = await _lineas_de(tenant.engine, res.entry.id)
    assert _cuadra(lineas)
    por_cuenta = {c: (d, a) for c, d, a in lineas}
    assert por_cuenta["310505"] == ("debit", Decimal("70000.00"))


async def test_apertura_idempotente_una_por_periodo(tenant):
    """Reintentar la apertura del mismo período devuelve el mismo asiento (replay), sin duplicar."""
    corte = CorteApertura(fecha=now_co(), caja=Decimal("100000"))
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await (await _apertura(s)).registrar_apertura(corte)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await (await _apertura(s)).registrar_apertura(corte)
        await s.commit()
    assert r1.replay is False and r2.replay is True
    assert r2.entry.id == r1.entry.id
    assert await _n_asientos(tenant.engine, "apertura") == 1


async def test_apertura_vacia_se_rechaza(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CorteVacio):
            await (await _apertura(s)).registrar_apertura(CorteApertura(fecha=now_co()))
        await s.rollback()
    assert await _n_asientos(tenant.engine) == 0


async def test_apertura_inmutable(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await (await _apertura(s)).registrar_apertura(
            CorteApertura(fecha=now_co(), caja=Decimal("100000"))
        )
        eid = res.entry.id
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(AsientoInmutable):
            await _ledger(s).anexar_linea(eid, None)
        await s.rollback()


async def test_apertura_aislamiento_multitenant(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await (await _apertura(s)).registrar_apertura(
            CorteApertura(fecha=now_co(), caja=Decimal("100000"))
        )
        await s.commit()
    assert await _n_asientos(a.engine) == 1
    assert await _n_asientos(b.engine) == 0


# --- CIERRE ------------------------------------------------------------------
async def _sembrar_resultado(s: AsyncSession):
    """Postea un 'ingreso' y un 'gasto' manuales en el período en curso (result accounts 4/5)."""
    led = _ledger(s)
    await _repo(s).asegurar_puc()
    await led.registrar_asiento(
        AsientoCrear(
            fecha=now_co(), origen_tipo="manual", lineas=[
                _linea("110505", "debit", "100000"), _linea("413505", "credit", "100000"),
            ],
        )
    )
    await led.registrar_asiento(
        AsientoCrear(
            fecha=now_co(), origen_tipo="manual", lineas=[
                _linea("519595", "debit", "30000"), _linea("110505", "credit", "30000"),
            ],
        )
    )


async def test_cierre_lleva_resultado_a_patrimonio(tenant):
    """Cerrar el período zeroing 4/5/6 contra patrimonio (utilidad = 100000 − 30000 = 70000)."""
    ahora = now_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar_resultado(s)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await (await _cierre(s)).cerrar_periodo(ahora.year, ahora.month)
        eid = res.entry.id
        await s.commit()

    lineas = await _lineas_de(tenant.engine, eid)
    assert _cuadra(lineas)
    por_cuenta = {c: (d, a) for c, d, a in lineas}
    # Ingreso (crédito 100000) se debita; gasto (débito 30000) se acredita.
    assert por_cuenta["413505"] == ("debit", Decimal("100000.00"))
    assert por_cuenta["519595"] == ("credit", Decimal("30000.00"))
    # Utilidad 70000 al crédito de patrimonio.
    assert por_cuenta["310505"] == ("credit", Decimal("70000.00"))

    # Las cuentas de resultado netean a cero tras el cierre.
    async with AsyncSession(tenant.engine) as s:
        for codigo in ("413505", "519595"):
            neto = (
                await s.execute(
                    text(
                        "SELECT COALESCE(SUM(CASE WHEN l.direction='debit' THEN l.amount ELSE -l.amount END),0) "
                        "FROM journal_line l JOIN puc_cuentas p ON p.id=l.cuenta_id WHERE p.codigo=:c"
                    ),
                    {"c": codigo},
                )
            ).scalar_one()
            assert neto == 0

    # El período quedó cerrado.
    async with AsyncSession(tenant.engine) as s:
        estado = (
            await s.execute(
                text("SELECT estado FROM periodo_contable WHERE anio=:a AND mes=:m"),
                {"a": ahora.year, "m": ahora.month},
            )
        ).scalar_one()
        assert estado == "closed"


async def test_cierre_bloquea_nuevos_asientos(tenant):
    """Tras el cierre, el período no admite más asientos (guard de período)."""
    ahora = now_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar_resultado(s)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await (await _cierre(s)).cerrar_periodo(ahora.year, ahora.month)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PeriodoBloqueado):
            await _ledger(s).registrar_asiento(
                AsientoCrear(
                    fecha=now_co(), origen_tipo="manual", lineas=[
                        _linea("110505", "debit", "1000"), _linea("413505", "credit", "1000"),
                    ],
                )
            )
        await s.rollback()


async def test_cierre_idempotente(tenant):
    """Cerrar dos veces el mismo período → un solo asiento de cierre (replay)."""
    ahora = now_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar_resultado(s)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await (await _cierre(s)).cerrar_periodo(ahora.year, ahora.month)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await (await _cierre(s)).cerrar_periodo(ahora.year, ahora.month)
        await s.commit()
    assert r1.replay is False and r2.replay is True
    assert r2.entry.id == r1.entry.id
    assert await _n_asientos(tenant.engine, "cierre") == 1


async def test_cierre_periodo_inexistente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PeriodoInexistente):
            await (await _cierre(s)).cerrar_periodo(1999, 1)
        await s.rollback()


async def test_cierre_inmutable(tenant):
    ahora = now_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar_resultado(s)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await (await _cierre(s)).cerrar_periodo(ahora.year, ahora.month)
        eid = res.entry.id
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(AsientoInmutable):
            await _ledger(s).anexar_linea(eid, None)
        await s.rollback()


# --- FACTURA DE PROVEEDOR "SUELTA" → CxP -------------------------------------
async def test_factura_proveedor_proyecta_cxp(tenant):
    """La factura suelta se asienta: débito compras/costo, crédito Proveedores (CxP)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _factura(s, "F-001", "500000")
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await (await _proyector(s)).proyectar_factura_proveedor("F-001")
        eid = res.entry.id
        await s.commit()
    lineas = await _lineas_de(tenant.engine, eid)
    assert _cuadra(lineas)
    por_cuenta = {c: (d, a) for c, d, a in lineas}
    assert por_cuenta["620501"] == ("debit", Decimal("500000.00"))
    assert por_cuenta["220505"] == ("credit", Decimal("500000.00"))


async def test_factura_proveedor_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _factura(s, "F-002", "500000")
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await (await _proyector(s)).proyectar_factura_proveedor("F-002")
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await (await _proyector(s)).proyectar_factura_proveedor("F-002")
        await s.commit()
    assert r1.replay is False and r2.replay is True
    assert r2.entry.id == r1.entry.id
    assert await _n_asientos(tenant.engine, "factura_proveedor") == 1


async def test_factura_proveedor_backfill_cxp_queda_por_el_pendiente(tenant):
    """Backfill: la factura acredita Proveedores y el abono lo debita → saldo = pendiente."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _factura(s, "F-003", "500000", pendiente="300000")
        await s.execute(
            text(
                "INSERT INTO facturas_abonos (factura_id, monto, fecha) "
                "VALUES ('F-003', 200000, CURRENT_DATE)"
            )
        )
        await s.commit()

    from core.config.timezone import rango_dia_co, today_co

    inicio, _ = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        resumen = await (await _proyector(s)).backfill(inicio)
        await s.commit()
    assert resumen.creados.get("factura_proveedor") == 1
    assert resumen.creados.get("abono_proveedor") == 1

    # Proveedores (220505, naturaleza crédito) = créditos − débitos = 500000 − 200000.
    async with AsyncSession(tenant.engine) as s:
        saldo = (
            await s.execute(
                text(
                    "SELECT COALESCE(SUM(CASE WHEN l.direction='credit' THEN l.amount ELSE -l.amount END),0) "
                    "FROM journal_line l JOIN puc_cuentas p ON p.id=l.cuenta_id WHERE p.codigo='220505'"
                )
            )
        ).scalar_one()
        assert saldo == Decimal("300000.00")


async def test_factura_proveedor_aislamiento_multitenant(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _factura(s, "F-A", "500000")
        await s.commit()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await (await _proyector(s)).proyectar_factura_proveedor("F-A")
        await s.commit()
    assert await _n_asientos(a.engine, "factura_proveedor") == 1
    assert await _n_asientos(b.engine, "factura_proveedor") == 0
