"""Vender la bolsa entera y el kilo suelto, contra UN solo inventario (TDD-primero).

La ferretería vende el mismo cemento de dos formas: el bulto de 50 kg a $28.000 y el kilo
menudeado a $1.500. Son dos precios, un solo montón de mercancía. Antes de esto el producto tenía
un solo `precio_venta` y no había dónde guardar el del bulto; peor, `contenido_paquete` (0069) le
cambiaba el significado a `precio_venta` y el kilo se habría cobrado a $30.

Ahora `precio_venta` es SIEMPRE el precio de una unidad de venta (un kilo, un gramo, una unidad) y
`precio_paquete` el del empaque completo. La línea de la venta dice `por_empaque=true` cuando se
vende el bulto, pero la cantidad sigue yendo en la unidad de venta (50, no 1): así inventario,
kárdex y factura DIAN nunca ven dos unidades distintas y la firma de idempotencia no cambia de
significado entre el request y lo persistido.

Invariante crítico: vender 1 bulto mueve el stock por `contenido_paquete` unidades con UN
movimiento de inventario. Nada mueve stock sin movimiento.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.ventas.errors import LineaInvalida
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


async def _cemento(s: AsyncSession, *, stock="200") -> int:
    """Cemento gris: se cuenta en kilos, se vende suelto a $1.500 o en bulto de 50 kg a $28.000."""
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_paquete, "
                "contenido_paquete, nombre_paquete, iva, permite_fraccion, activo) "
                "VALUES ('Cemento Gris','Kg',1500,28000,50,'bulto',0,false,true) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


async def _vendedor(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id")
        )
    ).scalar_one()


async def _movimientos(engine, pid: int) -> list[tuple[str, Decimal]]:
    async with AsyncSession(engine) as s:
        filas = (
            await s.execute(
                text(
                    "SELECT tipo, cantidad FROM movimientos_inventario "
                    "WHERE producto_id=:p ORDER BY id"
                ),
                {"p": pid},
            )
        ).all()
    return [(f.tipo, Decimal(f.cantidad)) for f in filas]


async def _stock(engine, pid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()


async def test_vender_un_bulto_mueve_el_contenido_con_un_solo_movimiento(tenant):
    """INVARIANTE: 1 bulto = 50 kg fuera del inventario, en UN movimiento SALIDA de 50."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _vendedor(s)
        pid = await _cemento(s, stock="200")
        await s.commit()

        await VentaService(SqlVentasRepository(s)).registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("50"), por_empaque=True)],
            ),
            vendedor_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("150.000")      # 200 − 50
    assert await _movimientos(tenant.engine, pid) == [("SALIDA", Decimal("50.000"))]


async def test_el_bulto_cobra_su_precio_y_el_kilo_suelto_el_suyo(tenant):
    """1 bulto = $28.000 (no 50 × $1.500). El kilo suelto sigue en $1.500."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _vendedor(s)
        pid = await _cemento(s)
        await s.commit()
        svc = VentaService(SqlVentasRepository(s))

        bulto = await svc.registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("50"), por_empaque=True)],
            ),
            vendedor_id=uid,
        )
        suelto = await svc.registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("3"))],
            ),
            vendedor_id=uid,
        )
        await s.commit()

    assert bulto.venta.total == Decimal("28000.00")     # el bulto, no 50 × 1.500 = 75.000
    assert suelto.venta.total == Decimal("4500.00")     # 3 kg × 1.500


async def test_bolsa_mas_kilos_sueltos_se_suman_sin_regla_rara(tenant):
    """La regla del dueño: el precio del bulto es fijo y los kilos sueltos van aparte."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _vendedor(s)
        pid = await _cemento(s)
        await s.commit()

        res = await VentaService(SqlVentasRepository(s)).registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[
                    VentaDetalleCrear(producto_id=pid, cantidad=Decimal("50"), por_empaque=True),
                    VentaDetalleCrear(producto_id=pid, cantidad=Decimal("3")),
                ],
            ),
            vendedor_id=uid,
        )
        await s.commit()

    assert res.venta.total == Decimal("32500.00")                      # 28.000 + 3 × 1.500
    assert await _stock(tenant.engine, pid) == Decimal("147.000")      # 200 − 50 − 3


async def test_tres_bultos_cobran_tres_veces_el_bulto(tenant):
    """El precio del empaque es fijo POR empaque: 3 bultos = 3 × $28.000, y salen 150 kg."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _vendedor(s)
        pid = await _cemento(s)
        await s.commit()

        res = await VentaService(SqlVentasRepository(s)).registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("150"), por_empaque=True)],
            ),
            vendedor_id=uid,
        )
        await s.commit()

    assert res.venta.total == Decimal("84000.00")
    assert await _stock(tenant.engine, pid) == Decimal("50.000")       # 200 − 150


async def test_cantidad_que_no_es_multiplo_del_empaque_es_error_de_dominio(tenant):
    """"Bulto y medio" no existe: se cobra el bulto y lo suelto en líneas aparte (regla del dueño)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _vendedor(s)
        pid = await _cemento(s)
        await s.commit()

        with pytest.raises(LineaInvalida):
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                VentaCrear(
                    metodo_pago="efectivo",
                    lineas=[VentaDetalleCrear(
                        producto_id=pid, cantidad=Decimal("75"), por_empaque=True
                    )],
                ),
                vendedor_id=uid,
            )


async def test_producto_sin_empaque_pedido_por_paquete_es_error_de_dominio(tenant, seed_producto):
    """Un martillo no se vende por bulto: error explícito, no un 500 ni un cobro inventado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()

        with pytest.raises(LineaInvalida):
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                VentaCrear(
                    metodo_pago="efectivo",
                    lineas=[VentaDetalleCrear(
                        producto_id=pid, cantidad=Decimal("50"), por_empaque=True
                    )],
                ),
                vendedor_id=uid,
            )


async def test_el_granel_cobra_igual_que_antes_con_el_precio_ya_por_gramo(tenant):
    """Puntilla tras 0072: `precio_venta` es por gramo ($15) y la caja de 500 g sigue en $7.500.

    Es la migración de significado: antes `precio_venta` era el precio de la CAJA y el motor dividía
    por la convención (GRM→500). Ahora el número está en la unidad de venta y no hay que adivinar.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _vendedor(s)
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_paquete, "
                    "contenido_paquete, nombre_paquete, iva, permite_fraccion, activo) "
                    "VALUES ('Puntilla 1\"','GRM',15,7500,500,'caja',0,false,true) RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,2000,0)"),
            {"p": pid},
        )
        await s.commit()
        svc = VentaService(SqlVentasRepository(s))

        gramos = await svc.registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("500"))],
            ),
            vendedor_id=uid,
        )
        caja = await svc.registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("500"), por_empaque=True)],
            ),
            vendedor_id=uid,
        )
        await s.commit()

    assert gramos.venta.total == Decimal("7500.00")
    assert caja.venta.total == Decimal("7500.00")
    assert await _stock(tenant.engine, pid) == Decimal("1000.000")     # 2.000 − 500 − 500
