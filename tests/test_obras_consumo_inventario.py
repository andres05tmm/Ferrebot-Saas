"""INVARIANTE CRÍTICO (test-primero): registrar un ConsumoInventario DEBE mover el inventario.

"Nada mueve inventario sin movimiento" (.claude/rules/development-workflow.md, carve-out). Registrar el
consumo de material de una obra genera —en la MISMA transacción— un `movimiento_inventario` (salida) y
baja el stock. El primer test es el invariante; el resto cubre la valuación del costo, la ATOMICIDAD
(stock insuficiente revierte el consumo, sin movimiento huérfano), el rechazo de consumo sobre obra
liquidada, el producto inexistente y el aislamiento entre empresas (la base es la frontera).

Se corre contra Postgres efímero (fixture `tenant`): el movimiento lo emite el service real de inventario.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.inventario.errors import AjusteDejaStockNegativo, ProductoInexistente
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.obra.errors import ConsumoEnObraLiquidada
from modules.obra.repository import SqlObrasRepository
from modules.obra.schemas import ConsumoInventarioCrear
from modules.obra.service import ObrasService


def _servicio(s: AsyncSession) -> ObrasService:
    """ObrasService cableado con el InventarioService real sobre la MISMA sesión (misma transacción)."""
    return ObrasService(
        SqlObrasRepository(s), inventario=InventarioService(SqlInventarioRepository(s))
    )


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
        )
    ).scalar_one()


async def _obra(s: AsyncSession, cid: int, *, estado: str = "EN_EJECUCION") -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, 'Vía La Paz', :e) RETURNING id"
            ),
            {"c": cid, "e": estado},
        )
    ).scalar_one()


async def _producto(s: AsyncSession, *, stock: str = "100", **cols) -> int:
    columnas = {
        "nombre": "Cemento", "unidad_medida": "bulto", "precio_venta": "30000",
        "iva": 19, "permite_fraccion": False, "activo": True, **cols,
    }
    nombres = ", ".join(columnas)
    binds = ", ".join(f":{k}" for k in columnas)
    pid = (
        await s.execute(
            text(f"INSERT INTO productos ({nombres}) VALUES ({binds}) RETURNING id"), columnas
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


async def _stock(engine, pid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()


async def _cuenta(engine, tabla: str, pid_col: str, pid: int) -> int:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text(f"SELECT count(*) FROM {tabla} WHERE {pid_col}=:p"), {"p": pid}
            )
        ).scalar_one()


async def test_consumo_genera_movimiento_y_baja_stock(tenant):
    """EL INVARIANTE: un consumo de 30 sobre stock 100 asienta un movimiento de inventario y deja 70."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        pid = await _producto(s, stock="100")
        await s.commit()

        consumo, resultado = await _servicio(s).registrar_consumo(
            oid, ConsumoInventarioCrear(producto_id=pid, cantidad=Decimal("30"), costo_unitario=Decimal("28000")),
            usuario_id=None,
        )
        await s.commit()

    assert consumo.obra_id == oid and consumo.cantidad == Decimal("30")
    assert resultado.movimiento_id is not None            # hubo movimiento
    assert resultado.stock_actual == Decimal("70.000")    # el stock bajó

    async with AsyncSession(tenant.engine) as s:
        tipo, cant = (
            await s.execute(
                text(
                    "SELECT tipo, cantidad FROM movimientos_inventario "
                    "WHERE producto_id=:p ORDER BY id DESC LIMIT 1"
                ),
                {"p": pid},
            )
        ).one()
        assert cant == Decimal("-30.000")                 # salida de stock (delta negativo)
    # el consumo quedó imputado y el stock final es 70 (nada movió inventario sin movimiento)
    assert await _cuenta(tenant.engine, "consumos_inventario", "obra_id", oid) == 1
    assert await _cuenta(tenant.engine, "movimientos_inventario", "producto_id", pid) == 1
    assert await _stock(tenant.engine, pid) == Decimal("70.000")


async def test_consumo_costo_por_defecto_desde_costo_promedio(tenant):
    """Sin `costo_unitario` explícito, el consumo se valora al costo promedio ponderado del producto."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        pid = await _producto(s, stock="50", costo_promedio="1500", precio_compra="1200")
        await s.commit()

        consumo, _ = await _servicio(s).registrar_consumo(
            oid, ConsumoInventarioCrear(producto_id=pid, cantidad=Decimal("10")), usuario_id=None
        )
        await s.commit()

    assert consumo.costo_unitario == Decimal("1500.0000")   # tomado del costo promedio


async def test_consumo_stock_insuficiente_revierte_todo(tenant):
    """ATOMICIDAD: si la salida deja stock negativo, ni el consumo ni el movimiento quedan (rollback)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        pid = await _producto(s, stock="5")
        await s.commit()

        with pytest.raises(AjusteDejaStockNegativo):
            await _servicio(s).registrar_consumo(
                oid, ConsumoInventarioCrear(producto_id=pid, cantidad=Decimal("10")), usuario_id=None
            )
        await s.rollback()

    assert await _cuenta(tenant.engine, "consumos_inventario", "obra_id", oid) == 0   # sin consumo
    assert await _cuenta(tenant.engine, "movimientos_inventario", "producto_id", pid) == 0  # sin movimiento
    assert await _stock(tenant.engine, pid) == Decimal("5.000")   # stock intacto


async def test_consumo_en_obra_liquidada_se_rechaza(tenant):
    """Una obra LIQUIDADA tiene el gasto real congelado: no admite más consumos (409)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid, estado="LIQUIDADA")
        pid = await _producto(s, stock="100")
        await s.commit()

        with pytest.raises(ConsumoEnObraLiquidada):
            await _servicio(s).registrar_consumo(
                oid, ConsumoInventarioCrear(producto_id=pid, cantidad=Decimal("10")), usuario_id=None
            )
        await s.rollback()

    assert await _cuenta(tenant.engine, "movimientos_inventario", "producto_id", pid) == 0


async def test_consumo_producto_inexistente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        await s.commit()

        with pytest.raises(ProductoInexistente):
            await _servicio(s).registrar_consumo(
                oid, ConsumoInventarioCrear(producto_id=99999, cantidad=Decimal("1")), usuario_id=None
            )
        await s.rollback()


async def test_consumo_aislado_entre_empresas(tenant_factory):
    """El consumo (y su movimiento) de la empresa A jamás toca el inventario de la B (bases distintas)."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra(s, cid)
        pid_a = await _producto(s, stock="100")
        await s.commit()
        await _servicio(s).registrar_consumo(
            oid, ConsumoInventarioCrear(producto_id=pid_a, cantidad=Decimal("40")), usuario_id=None
        )
        await s.commit()

    async with AsyncSession(empresa_b.engine, expire_on_commit=False) as s:
        pid_b = await _producto(s, stock="100")   # mismo id lógico, base distinta
        await s.commit()

    assert await _stock(empresa_a.engine, pid_a) == Decimal("60.000")   # A consumió
    assert await _stock(empresa_b.engine, pid_b) == Decimal("100.000")  # B intacto
    assert await _cuenta(empresa_b.engine, "movimientos_inventario", "producto_id", pid_b) == 0
