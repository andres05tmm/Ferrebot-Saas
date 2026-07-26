"""Corrección de una compra ya recibida — invariantes de stock y dinero (TDD-primero).

El dueño digita mal una cantidad o un costo y lo ve al día siguiente. Corregir NO puede ser un
UPDATE silencioso: cada diferencia de cantidad deja su movimiento AJUSTE en el kárdex (regla #7) y
la plata se concilia sola (caja o cuenta por pagar). Lo que se prueba:

- subir/bajar cantidad → AJUSTE con el delta exacto y stock final correcto;
- corregir SOLO el costo → cero movimientos de inventario, pero total y costo promedio al día;
- dos correcciones seguidas → dos ajustes (la segunda no puede ser replay de la primera);
- misma key → replay sin efectos; misma key con otras líneas → conflicto;
- la diferencia de plata sale/entra de la caja cuando se pide, y la CxP se recalcula;
- lo que NO se puede corregir: compra de obra/viaje, sobrepago de la factura, sin destino del
  diferencial, caja cerrada (todo sin efectos parciales).

Integración contra base efímera real (fixture `tenant`).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from core.config.timezone import today_co
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.errors import (
    CompraInexistente,
    CompraNoCorregible,
    CorreccionInvalida,
    IdempotenciaConflicto,
)
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import (
    CompraCorregir,
    CompraCrear,
    CompraItemCrear,
    LineaCorreccion,
    ProveedorRef,
)
from modules.compras.service import ComprasService
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.proveedores.repository import SqlProveedoresRepository


def _svc(s: AsyncSession) -> ComprasService:
    return ComprasService(
        SqlComprasRepository(s),
        proveedores=SqlProveedoresRepository(s),
        inventario=InventarioService(SqlInventarioRepository(s)),
        caja=CajaService(SqlCajaRepository(s)),
    )


def _compra(pid: int, *, cantidad="10", costo="5000", **extra) -> CompraCrear:
    return CompraCrear(
        proveedor=ProveedorRef(nombre="Ferrisariato"),
        items=[CompraItemCrear(producto_id=pid, cantidad=Decimal(cantidad), costo=Decimal(costo))],
        **extra,
    )


def _correccion(pid: int, *, cantidad="12", costo="5000", motivo="digité mal", **extra):
    return CompraCorregir(
        lineas=[LineaCorreccion(producto_id=pid, cantidad=Decimal(cantidad), costo=Decimal(costo))],
        motivo=motivo,
        **extra,
    )


async def _stock(engine, pid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()


async def _ajustes(engine) -> list[Decimal]:
    async with AsyncSession(engine) as s:
        filas = (
            await s.execute(
                text(
                    "SELECT cantidad FROM movimientos_inventario WHERE tipo='AJUSTE' ORDER BY id"
                )
            )
        ).scalars().all()
    return [Decimal(f) for f in filas]


async def _compra_fila(engine, compra_id: int):
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT total, correcciones, corregida_en FROM compras WHERE id=:c"),
                {"c": compra_id},
            )
        ).one()


# --- Stock: la corrección se aplica por diferencia, siempre con su movimiento ------------------

async def test_subir_cantidad_deja_ajuste_con_el_delta(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()
    assert await _stock(tenant.engine, pid) == Decimal("10.000")

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).corregir(compra.id, _correccion(pid, cantidad="12"), usuario_id=uid)
        await s.commit()

    assert res.delta_total == Decimal("10000.00")            # 2 unidades × 5.000
    assert await _ajustes(tenant.engine) == [Decimal("2.000")]
    assert await _stock(tenant.engine, pid) == Decimal("12.000")
    total, correcciones, corregida_en = await _compra_fila(tenant.engine, compra.id)
    assert Decimal(total) == Decimal("60000.00") and correcciones == 1 and corregida_en is not None


async def test_bajar_cantidad_deja_ajuste_negativo(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).corregir(compra.id, _correccion(pid, cantidad="7"), usuario_id=uid)
        await s.commit()

    assert await _ajustes(tenant.engine) == [Decimal("-3.000")]
    assert await _stock(tenant.engine, pid) == Decimal("7.000")


async def test_corregir_solo_el_costo_no_toca_el_inventario(tenant, seed_producto):
    """El error fue de precio, no de cantidad: nada que ajustar en el kárdex, pero el costo del
    producto y el total de la compra sí cambian."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).corregir(
            compra.id, _correccion(pid, cantidad="10", costo="4000"), usuario_id=uid
        )
        await s.commit()

    assert res.delta_total == Decimal("-10000.00")
    assert await _ajustes(tenant.engine) == []
    assert await _stock(tenant.engine, pid) == Decimal("10.000")
    async with AsyncSession(tenant.engine) as s:
        precio, promedio = (
            await s.execute(
                text("SELECT precio_compra, costo_promedio FROM productos WHERE id=:p"), {"p": pid}
            )
        ).one()
    assert Decimal(precio) == Decimal("4000.00")
    assert Decimal(promedio) == Decimal("4000.00")


async def test_dos_correcciones_seguidas_aplican_las_dos(tenant, seed_producto):
    """La segunda corrección NO puede ser replay de la primera (por eso la key lleva el contador)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).corregir(compra.id, _correccion(pid, cantidad="12"), usuario_id=uid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).corregir(compra.id, _correccion(pid, cantidad="15"), usuario_id=uid)
        await s.commit()

    assert await _ajustes(tenant.engine) == [Decimal("2.000"), Decimal("3.000")]
    assert await _stock(tenant.engine, pid) == Decimal("15.000")
    _total, correcciones, _ = await _compra_fila(tenant.engine, compra.id)
    assert correcciones == 2


# --- Idempotencia ------------------------------------------------------------

async def test_misma_key_es_replay_y_payload_distinto_es_conflicto(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    datos = _correccion(pid, cantidad="12", idempotency_key="corr-1")
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).corregir(compra.id, datos, usuario_id=uid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).corregir(compra.id, datos, usuario_id=uid)   # doble clic
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert await _ajustes(tenant.engine) == [Decimal("2.000")]          # UN solo ajuste

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(IdempotenciaConflicto):
            await _svc(s).corregir(
                compra.id, _correccion(pid, cantidad="99", idempotency_key="corr-1"), usuario_id=uid
            )
        await s.rollback()
    assert await _stock(tenant.engine, pid) == Decimal("12.000")


# --- Dinero ------------------------------------------------------------------

async def test_diferencia_sale_de_la_caja_cuando_se_pide(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("200000"))
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).corregir(
            compra.id, _correccion(pid, cantidad="12", ajustar_pago=True), usuario_id=uid
        )
        await s.commit()

    assert res.movimiento_caja_id is not None
    async with AsyncSession(tenant.engine) as s:
        tipo, monto, referencia = (
            await s.execute(
                text("SELECT tipo, monto, referencia FROM caja_movimientos WHERE id=:i"),
                {"i": res.movimiento_caja_id},
            )
        ).one()
    assert tipo == "egreso" and Decimal(monto) == Decimal("10000.00")
    assert referencia == f"compra:{compra.id}"


async def test_diferencia_a_favor_entra_a_la_caja(tenant, seed_producto):
    """Costó menos de lo registrado: el proveedor devuelve, entra plata."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("200000"))
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).corregir(
            compra.id, _correccion(pid, cantidad="10", costo="4000", ajustar_pago=True),
            usuario_id=uid
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        tipo, monto = (
            await s.execute(
                text("SELECT tipo, monto FROM caja_movimientos WHERE id=:i"),
                {"i": res.movimiento_caja_id},
            )
        ).one()
    assert tipo == "ingreso" and Decimal(monto) == Decimal("10000.00")


async def test_cxp_se_recalcula_y_no_admite_sobrepago(tenant, seed_producto):
    """La compra fue a crédito: corregir mueve el pendiente. Si el nuevo total queda por debajo de
    lo ya abonado, se rechaza (no se inventa un saldo a favor)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (
            await _svc(s).registrar(
                _compra(pid, a_credito=True, numero_factura="F-CORR"), usuario_id=uid
            )
        ).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).corregir(compra.id, _correccion(pid, cantidad="12"), usuario_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        total, pendiente = (
            await s.execute(
                text("SELECT total, pendiente FROM facturas_proveedores WHERE id='F-CORR'")
            )
        ).one()
    assert Decimal(total) == Decimal("60000.00") and Decimal(pendiente) == Decimal("60000.00")

    # Se abona 55.000 y luego se intenta bajar la compra a 35.000 (7 × 5.000): sobrepago → rechazo.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await SqlProveedoresRepository(s).crear_abono_y_recalcular(
            factura_id="F-CORR", monto=Decimal("55000"), fecha=today_co()
        )
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CorreccionInvalida):
            await _svc(s).corregir(compra.id, _correccion(pid, cantidad="7"), usuario_id=uid)
        await s.rollback()
    assert await _stock(tenant.engine, pid) == Decimal("12.000")     # sin efectos parciales


async def test_ajustar_pago_sin_caja_abierta_no_deja_efectos(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        compra = (await _svc(s).registrar(_compra(pid), usuario_id=uid)).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CajaNoAbierta):
            await _svc(s).corregir(
                compra.id, _correccion(pid, cantidad="12", ajustar_pago=True), usuario_id=uid
            )
        await s.rollback()

    assert await _ajustes(tenant.engine) == []
    assert await _stock(tenant.engine, pid) == Decimal("10.000")


# --- Lo que no se corrige ----------------------------------------------------

async def test_compra_inexistente(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        with pytest.raises(CompraInexistente):
            await _svc(s).corregir(99_999, _correccion(pid), usuario_id=uid)


async def test_compra_de_obra_no_es_corregible(tenant, seed_producto):
    """Una compra imputada a obra no movió stock: corregirla por diferencia no aplica."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        cid = (
            await s.execute(
                text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
            )
        ).scalar_one()
        obra_id = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Obra 1') RETURNING id"),
                {"c": cid},
            )
        ).scalar_one()
        await s.commit()
        compra = (
            await _svc(s).registrar(
                CompraCrear(
                    proveedor=ProveedorRef(nombre="Cantera"),
                    items=[CompraItemCrear(cantidad=Decimal("5"), costo=Decimal("1000"))],
                    obra_id=obra_id, categoria="ARENA_AGREGADO",
                ),
                usuario_id=uid,
            )
        ).compra
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CompraNoCorregible):
            await _svc(s).corregir(compra.id, _correccion(pid), usuario_id=uid)
        await s.rollback()
