"""Inventario contra base efímera: precio (motor sobre datos reales), ajuste y stock."""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.inventario.errors import AjusteDejaStockNegativo
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService


async def _producto(s: AsyncSession, nombre="Cemento", precio="1000", **cols) -> int:
    columnas = {"nombre": nombre, "unidad_medida": "unidad", "precio_venta": precio,
                "iva": 19, "permite_fraccion": False, "activo": True, **cols}
    nombres = ", ".join(columnas)
    binds = ", ".join(f":{k}" for k in columnas)
    return (
        await s.execute(
            text(f"INSERT INTO productos ({nombres}) VALUES ({binds}) RETURNING id"), columnas
        )
    ).scalar_one()


async def _inventario(s: AsyncSession, pid: int, stock="0", minimo="0") -> None:
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,:m)"),
        {"p": pid, "s": stock, "m": minimo},
    )


def _svc(s):
    return InventarioService(SqlInventarioRepository(s))


async def test_precio_escalonado_desde_db(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(
            s, precio="5000", precio_umbral="10",
            precio_bajo_umbral="5000", precio_sobre_umbral="4500",
        )
        await s.commit()
        bajo = await _svc(s).calcular_precio(pid, Decimal("5"))
        sobre = await _svc(s).calcular_precio(pid, Decimal("12"))

    assert (bajo.total, bajo.precio_unitario, bajo.regla) == (Decimal("25000.00"), Decimal("5000"), "escalonado")
    assert (sobre.total, sobre.precio_unitario, sobre.regla) == (Decimal("54000.00"), Decimal("4500"), "escalonado")


async def test_precio_fraccion_desde_db(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s, precio="1000", permite_fraccion=True)
        await s.execute(
            text(
                "INSERT INTO productos_fracciones (producto_id, fraccion, decimal, precio_total) "
                "VALUES (:p, '1/2', 0.5, 600)"
            ),
            {"p": pid},
        )
        await s.commit()
        calc = await _svc(s).calcular_precio(pid, Decimal("0.5"))

    assert calc.total == Decimal("600.00")
    assert calc.regla == "fraccion"


async def test_ajuste_aplica_y_registra_kardex(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s)
        await _inventario(s, pid, stock="10")
        await s.commit()
        res = await _svc(s).ajustar(producto_id=pid, delta=Decimal("5"), motivo="conteo", usuario_id=None)
        await s.commit()

    assert res.replay is False
    assert res.stock_actual == Decimal("15.000")

    async with AsyncSession(tenant.engine) as s:
        tipo, cant = (
            await s.execute(text("SELECT tipo, cantidad FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid})
        ).one()
        assert tipo == "AJUSTE"
        assert cant == Decimal("5.000")
        kardex = await SqlInventarioRepository(s).kardex(pid)
        assert len(kardex) == 1 and kardex[0].tipo == "AJUSTE"


async def test_ajuste_idempotente_devuelve_mismo_movimiento(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s)
        await _inventario(s, pid, stock="10")
        await s.commit()
        r1 = await _svc(s).ajustar(producto_id=pid, delta=Decimal("5"), motivo="conteo", usuario_id=None, idempotency_key="k1")
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).ajustar(producto_id=pid, delta=Decimal("5"), motivo="conteo", usuario_id=None, idempotency_key="k1")
        await s.commit()

    assert r2.replay is True
    assert r2.movimiento_id == r1.movimiento_id          # el replay devuelve el movimiento original
    async with AsyncSession(tenant.engine) as s:
        # Un solo movimiento; la key vive en la columna dedicada y la referencia es solo el motivo.
        rows = (
            await s.execute(
                text("SELECT idempotency_key, referencia FROM movimientos_inventario WHERE producto_id=:p"),
                {"p": pid},
            )
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == "k1"
        assert rows[0][1] == "conteo"
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("15.000")   # aplicado una sola vez


async def test_ajuste_que_deja_stock_negativo_se_rechaza(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s)
        await _inventario(s, pid, stock="5")
        await s.commit()
        with pytest.raises(AjusteDejaStockNegativo):
            await _svc(s).ajustar(producto_id=pid, delta=Decimal("-10"), motivo="merma", usuario_id=None)
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid})).scalar_one() == 0
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("5.000")


async def test_stock_bajo_filtra(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        bajo = await _producto(s, nombre="Bajo")
        ok = await _producto(s, nombre="OK")
        await _inventario(s, bajo, stock="2", minimo="5")
        await _inventario(s, ok, stock="50", minimo="5")
        await s.commit()
        repo = SqlInventarioRepository(s)
        solo_bajo = await repo.listar_stock(solo_bajo=True)
        todos = await repo.listar_stock(solo_bajo=False)

    assert {f["producto_id"] for f in solo_bajo} == {bajo}
    assert solo_bajo[0]["bajo"] is True
    assert len(todos) == 2
