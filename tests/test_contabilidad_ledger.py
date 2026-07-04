"""Motor contable — ledger de doble partida + PUC (ADR 0030). Invariantes TDD test-primero.

Invariantes críticos (contra base efímera real):
- débitos = créditos: no se puede postear descuadrado.
- inmutabilidad: un asiento posted no se edita; corregir = asiento espejo (reversar).
- período bloqueado rechaza posting.
- proyector idempotente: mismo evento dos veces → un solo asiento.
- aislamiento multi-tenant.
- el arqueo de caja NO cambia al introducir el ledger (capa derivada).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.contabilidad.errors import (
    AsientoDescuadrado,
    AsientoInmutable,
    PeriodoBloqueado,
)
from modules.contabilidad.fuente_repository import FuenteContableRepository
from modules.contabilidad.ledger import LedgerService
from modules.contabilidad.proyector import Proyector
from modules.contabilidad.repository import SqlContabilidadRepository
from modules.contabilidad.schemas import AsientoCrear, LineaAsiento
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


# --- helpers -----------------------------------------------------------------
async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id"))
    ).scalar_one()


async def _producto(s: AsyncSession, *, precio="20000", costo="12000", stock="100") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
                "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',:pv,:pc,:cp,19,false,true) RETURNING id"
            ),
            {"pv": precio, "pc": costo, "cp": costo},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cliente', 0) RETURNING id"))
    ).scalar_one()


def _venta(pid, cantidad, *, metodo="efectivo", cliente_id=None):
    return VentaCrear(
        metodo_pago=metodo, cliente_id=cliente_id,
        lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal(cantidad))],
    )


def _ledger(s: AsyncSession) -> LedgerService:
    return LedgerService(SqlContabilidadRepository(s))


async def _proyector(s: AsyncSession) -> Proyector:
    repo = SqlContabilidadRepository(s)
    await repo.asegurar_puc()
    return Proyector(LedgerService(repo), FuenteContableRepository(s))


def _linea(codigo, direction, amount):
    return LineaAsiento(cuenta_codigo=codigo, direction=direction, amount=Decimal(amount))


async def _n_asientos(engine, origen=None) -> int:
    async with AsyncSession(engine) as s:
        q = "SELECT count(*) FROM journal_entry"
        if origen:
            q += f" WHERE origen_tipo='{origen}'"
        return (await s.execute(text(q))).scalar_one()


from datetime import datetime

from core.config.timezone import now_co


# --- INVARIANTE 1: débitos = créditos ----------------------------------------
async def test_no_se_puede_postear_descuadrado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlContabilidadRepository(s)
        await repo.asegurar_puc()
        with pytest.raises(AsientoDescuadrado):
            await LedgerService(repo).registrar_asiento(
                AsientoCrear(
                    fecha=now_co(), origen_tipo="manual", lineas=[
                        _linea("110505", "debit", "100"),
                        _linea("413505", "credit", "90"),
                    ],
                )
            )
        await s.commit()
    assert await _n_asientos(tenant.engine) == 0   # nada se persistió


# --- INVARIANTE 2: inmutabilidad + corrección por espejo ----------------------
async def test_asiento_posted_es_inmutable_y_se_corrige_con_espejo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        led = _ledger(s)
        await SqlContabilidadRepository(s).asegurar_puc()
        res = await led.registrar_asiento(
            AsientoCrear(
                fecha=now_co(), origen_tipo="manual", lineas=[
                    _linea("110505", "debit", "50000"),
                    _linea("413505", "credit", "50000"),
                ],
            )
        )
        eid = res.entry.id
        await s.commit()

    # Editar un asiento posteado → error.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(AsientoInmutable):
            await _ledger(s).anexar_linea(eid, None)
        await s.rollback()

    # Corregir = reversar (espejo con direcciones invertidas).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        rev = await _ledger(s).reversar(eid, motivo="anulación")
        await s.commit()
        assert rev.entry.reverso_de == eid

    async with AsyncSession(tenant.engine) as s:
        orig = (await s.execute(
            text("SELECT direction, amount FROM journal_line WHERE entry_id=:e ORDER BY orden"), {"e": eid}
        )).all()
        esp = (await s.execute(
            text("SELECT direction, amount FROM journal_line WHERE entry_id=:e ORDER BY orden"),
            {"e": rev.entry.id},
        )).all()
        assert [d for d, _ in orig] == ["debit", "credit"]
        assert [d for d, _ in esp] == ["credit", "debit"]   # invertido
        # El original quedó intacto (posted, mismas líneas).
        assert (await s.execute(text("SELECT estado FROM journal_entry WHERE id=:e"), {"e": eid})).scalar_one() == "posted"


# --- INVARIANTE 3: período bloqueado rechaza posting -------------------------
async def test_periodo_bloqueado_rechaza_posting(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        led = _ledger(s)
        await SqlContabilidadRepository(s).asegurar_puc()
        await led.registrar_asiento(
            AsientoCrear(
                fecha=now_co(), origen_tipo="manual", lineas=[
                    _linea("110505", "debit", "1000"), _linea("413505", "credit", "1000"),
                ],
            )
        )
        await s.commit()

    # Bloquear el período del mes en curso.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await s.execute(text("UPDATE periodo_contable SET estado='locked'"))
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


# --- INVARIANTE 4: proyector idempotente -------------------------------------
async def test_proyector_venta_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s)
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await (await _proyector(s)).proyectar_venta(venta.id)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await (await _proyector(s)).proyectar_venta(venta.id)
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert r2.entry.id == r1.entry.id
    assert await _n_asientos(tenant.engine, "venta") == 1


# --- INVARIANTE 5: aislamiento multi-tenant ----------------------------------
async def test_aislamiento_multitenant(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s)
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await (await _proyector(s)).proyectar_venta(venta.id)
        await s.commit()

    assert await _n_asientos(a.engine) == 1
    assert await _n_asientos(b.engine) == 0
    # La base B ni siquiera tiene PUC sembrado por A.
    async with AsyncSession(b.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM puc_cuentas"))).scalar_one() == 0


# --- INVARIANTE 6: el arqueo de caja NO cambia con el ledger ------------------
async def test_arqueo_no_cambia_al_introducir_el_ledger(tenant_factory):
    """Mismos movimientos en dos tenants; en uno se proyecta el ledger. El arqueo es idéntico."""
    async def operar(tenant, *, con_ledger: bool):
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            uid = await _usuario(s)
            pid = await _producto(s)
            await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
            venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
            await CajaService(SqlCajaRepository(s)).registrar_gasto(
                usuario_id=uid, monto=Decimal("10000"), categoria="otros", concepto="varios"
            )
            await s.commit()
        if con_ledger:
            async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
                proj = await _proyector(s)
                await proj.proyectar_venta(venta.id)
                await s.commit()
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            caja = await CajaService(SqlCajaRepository(s)).cerrar(usuario_id=uid, saldo_contado=Decimal("100000"))
            await s.commit()
        return caja

    control = await operar(await tenant_factory(), con_ledger=False)
    con = await operar(await tenant_factory(), con_ledger=True)
    assert con.saldo_esperado == control.saldo_esperado
    assert con.diferencia == control.diferencia
