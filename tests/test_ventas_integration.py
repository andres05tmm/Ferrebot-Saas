"""Integración del repositorio de ventas contra una base efímera real (Postgres)."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.ventas.errors import StockInsuficiente
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _venta(producto_id, cantidad, key=None):
    return VentaCrear(
        metodo_pago="efectivo",
        idempotency_key=key,
        lineas=[VentaDetalleCrear(producto_id=producto_id, cantidad=Decimal(cantidad))],
    )


async def test_registrar_venta_persiste_detalle_movimiento_y_descuenta_stock(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="11900", iva=19, stock="100")
        res = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "2"), vendedor_id=uid)
        await s.commit()

    assert res.replay is False
    assert res.venta.consecutivo == 1            # primer nextval de la SEQUENCE
    assert res.venta.total == Decimal("23800.00")

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        assert (await s.execute(text("SELECT count(*) FROM ventas_detalle"))).scalar_one() == 1
        tipo, cant = (
            await s.execute(text("SELECT tipo, cantidad FROM movimientos_inventario"))
        ).one()
        assert tipo == "SALIDA"
        assert cant == Decimal("2.000")
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("98.000")   # 100 - 2


async def test_idempotencia_no_duplica_venta_ni_movimiento(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        r1 = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3", key="dup"), uid)
        await s.commit()

    # Reintento con la MISMA clave en una sesión nueva: devuelve la existente, no crea otra.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3", key="dup"), uid)
        await s.commit()

    assert r2.replay is True
    assert r2.venta.id == r1.venta.id

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        assert (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one() == 1
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("97.000")   # se descontó una sola vez (100 - 3)


async def test_stock_insuficiente_no_registra_nada_en_modo_estricto(tenant, seed_producto):
    """Modo ESTRICTO (opt-in): vender más que el stock bloquea y no registra nada (stock intacto)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="5")
        with pytest.raises(StockInsuficiente):
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                _venta(pid, "10"), uid, control_stock_estricto=True
            )
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 0
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("5.000")   # intacto


async def test_default_permisivo_vende_y_deja_stock_negativo(tenant, seed_producto):
    """Default PERMISIVO: vender más que el stock SÍ registra la venta y el stock queda NEGATIVO."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="5")
        res = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "10"), uid)
        await s.commit()

    assert res.replay is False
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        tipo, cant = (
            await s.execute(text("SELECT tipo, cantidad FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid})
        ).one()
        assert tipo == "SALIDA" and cant == Decimal("10.000")
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("-5.000")   # 5 - 10: negativo OK (se corrige con la compra faltante)


async def test_listar_filtra_por_fecha_y_vendedor(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        otro = (
            await s.execute(
                text("INSERT INTO usuarios (nombre, rol) VALUES ('Otro','vendedor') RETURNING id")
            )
        ).scalar_one()
        svc = VentaService(SqlVentasRepository(s))
        await svc.registrar_venta(_venta(pid, "1", key="a"), vendedor_id=uid)
        await svc.registrar_venta(_venta(pid, "1", key="b"), vendedor_id=uid)
        await svc.registrar_venta(_venta(pid, "1", key="c"), vendedor_id=otro)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        repo = SqlVentasRepository(s)
        hoy_uid = await repo.listar(vendedor_id=uid)          # default = hoy Colombia
        hoy_todas = await repo.listar(vendedor_id=None)
        ayer = today_co() - timedelta(days=1)
        rango_pasado = await repo.listar(desde=ayer, hasta=ayer)

    assert len(hoy_uid) == 2
    assert {v.vendedor_id for v in hoy_uid} == {uid}          # scoping por vendedor
    assert len(hoy_todas) == 3                                # admin (None) ve todas
    assert rango_pasado == []                                 # las ventas son de hoy


async def test_venta_catalogo_hila_costo_de_compra_a_la_salida(tenant):
    """Costo de ventas exacto (opción C): el SALIDA guarda el precio_compra del producto al vender."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, iva, "
                    "permite_fraccion, activo) VALUES ('Cemento','unidad',20000,12000,19,false,true) "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,100,0)"),
            {"p": pid},
        )
        await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        tipo, cant, costo = (
            await s.execute(
                text("SELECT tipo, cantidad, costo_unitario FROM movimientos_inventario WHERE producto_id=:p"),
                {"p": pid},
            )
        ).one()
        assert tipo == "SALIDA"
        assert cant == Decimal("3.000")
        assert costo == Decimal("12000.00")   # precio_compra hilado al movimiento al momento de vender


async def test_venta_varia_no_genera_movimiento_ni_costo(tenant):
    """Línea varia (sin producto_id) no mueve inventario → no hay costo (no hay mercancía)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        datos = VentaCrear(
            metodo_pago="efectivo",
            lineas=[VentaDetalleCrear(descripcion="Corte de llave", precio_unitario=Decimal("5000"), cantidad=Decimal("1"))],
        )
        await VentaService(SqlVentasRepository(s)).registrar_venta(datos, vendedor_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        n = (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one()
        assert n == 0


async def test_obtener_trae_lineas(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="11900", iva=19, stock="100")
        res = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "2"), vendedor_id=uid)
        await s.commit()
        venta_id = res.venta.id

    async with AsyncSession(tenant.engine) as s:
        detalle = await SqlVentasRepository(s).obtener(venta_id)

    assert detalle is not None
    assert detalle.id == venta_id
    assert len(detalle.lineas) == 1                           # el detalle carga sus líneas
    linea = detalle.lineas[0]
    assert linea.producto_id == pid
    assert linea.cantidad == Decimal("2.000")
    assert linea.iva == 19
