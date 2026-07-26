"""Comprar en la unidad del PROVEEDOR y vender menudeado — invariante de stock (TDD-primero).

La ferretería compra una caja de puntillas y la vende por gramos. En el catálogo esos productos se
venden por SUB-UNIDAD (`unidad_medida` GRM/Cms/MLT): el precio es el del paquete y la venta descuenta
gramos/cm/ml. Hasta hoy la compra sumaba el número tal cual: registrar "10 cajas" metía 10 GRAMOS al
inventario y el costo quedaba en $/caja contra un COGS en $/gramo.

Ahora la línea dice EN QUÉ UNIDAD se está capturando (`unidad='paquete'` o `'sub'`) y el servicio
convierte con el mismo divisor del motor de precios (`unidades_por_paquete`): 10 cajas → 5.000 g y
el costo de la caja → costo por gramo. Un producto que no es granel no se convierte nunca.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import CompraCrear, CompraItemCrear, ProveedorRef
from modules.compras.service import ComprasService
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService


async def _producto_granel(s: AsyncSession, *, unidad="GRM", precio="7000") -> int:
    """Puntilla de a caja: se vende por gramo y el precio es el de la caja (500 g)."""
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
                "activo) VALUES ('Puntilla 1\"', :u, :p, 0, false, true) RETURNING id"
            ),
            {"u": unidad, "p": precio},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 0, 0)"),
        {"p": pid},
    )
    return pid


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Dueño','admin') RETURNING id")
        )
    ).scalar_one()


async def _stock(engine, pid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()


async def test_comprar_cajas_suma_gramos_al_inventario(tenant):
    """10 cajas de puntilla a $6.000 la caja → 5.000 g en stock y $12 por gramo de costo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto_granel(s)
        await s.commit()

        await ComprasService(SqlComprasRepository(s)).registrar(
            CompraCrear(
                proveedor=ProveedorRef(nombre="Ferrisariato"),
                items=[CompraItemCrear(
                    producto_id=pid, cantidad=Decimal("10"), costo=Decimal("6000"),
                    unidad="paquete",
                )],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("5000.000")     # 10 cajas × 500 g
    async with AsyncSession(tenant.engine) as s:
        precio, promedio = (
            await s.execute(
                text("SELECT precio_compra, costo_promedio FROM productos WHERE id=:p"), {"p": pid}
            )
        ).one()
        cantidad, costo, total = (
            await s.execute(
                text(
                    "SELECT d.cantidad, d.costo, c.total FROM compras_detalle d "
                    "JOIN compras c ON c.id = d.compra_id WHERE d.producto_id=:p"
                ),
                {"p": pid},
            )
        ).one()
    assert Decimal(precio) == Decimal("12.00") and Decimal(promedio) == Decimal("12.00")
    # El detalle queda en la MISMA unidad que el stock y la venta (gramos): una sola verdad.
    assert Decimal(cantidad) == Decimal("5000.000") and Decimal(costo) == Decimal("12.00")
    assert Decimal(total) == Decimal("60000.00")   # lo que de verdad se pagó por las 10 cajas


async def test_comprar_en_gramos_sigue_funcionando_igual(tenant):
    """Sin decir la unidad (o diciendo 'sub'), la cantidad es la sub-unidad: comportamiento de antes."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto_granel(s)
        await s.commit()

        await ComprasService(SqlComprasRepository(s)).registrar(
            CompraCrear(
                proveedor=ProveedorRef(nombre="Ferrisariato"),
                items=[CompraItemCrear(
                    producto_id=pid, cantidad=Decimal("500"), costo=Decimal("12")
                )],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("500.000")


async def test_producto_por_unidad_no_se_convierte(tenant, seed_producto):
    """Un martillo se compra y se vende por unidad: 'paquete' no tiene divisor y no cambia nada."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()

        await ComprasService(SqlComprasRepository(s)).registrar(
            CompraCrear(
                proveedor=ProveedorRef(nombre="Ferrisariato"),
                items=[CompraItemCrear(
                    producto_id=pid, cantidad=Decimal("10"), costo=Decimal("5000"),
                    unidad="paquete",
                )],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("10.000")


async def test_conteo_fisico_en_cajas_deja_el_stock_en_gramos(tenant):
    """"Conté 3 cajas" → 1.500 g, con su movimiento AJUSTE (regla #7)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto_granel(s)
        await s.commit()

        await InventarioService(SqlInventarioRepository(s)).contar(
            producto_id=pid, cantidad_contada=Decimal("3"), unidad="paquete",
            motivo="conteo físico", usuario_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("1500.000")
    async with AsyncSession(tenant.engine) as s:
        tipo, cantidad = (
            await s.execute(
                text(
                    "SELECT tipo, cantidad FROM movimientos_inventario WHERE producto_id=:p "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"p": pid},
            )
        ).one()
    assert tipo == "AJUSTE" and Decimal(cantidad) == Decimal("1500.000")


async def test_bolsa_de_cal_de_25_kg_es_dato_del_producto(tenant):
    """La cal se compra por bolsa de 25 kg y se vende por kilo. El tamaño del empaque NO se puede
    adivinar por la unidad (un bulto de cemento trae 50): es dato del producto (`contenido_paquete`).
    2 bolsas a $35.000 → 50 kg en stock a $1.400 el kilo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo, contenido_paquete, nombre_paquete) "
                    "VALUES ('Cal', 'Kg', 2000, 0, false, true, 25, 'bolsa') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,0,0)"),
            {"p": pid},
        )
        await s.commit()

        await ComprasService(SqlComprasRepository(s)).registrar(
            CompraCrear(
                proveedor=ProveedorRef(nombre="Ferrisariato"),
                items=[CompraItemCrear(
                    producto_id=pid, cantidad=Decimal("2"), costo=Decimal("35000"),
                    unidad="paquete",
                )],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("50.000")     # 2 bolsas × 25 kg
    async with AsyncSession(tenant.engine) as s:
        promedio = (
            await s.execute(text("SELECT costo_promedio FROM productos WHERE id=:p"), {"p": pid})
        ).scalar_one()
    assert Decimal(promedio) == Decimal("1400.00")                   # $35.000 / 25 kg


async def test_el_conteo_de_bultos_usa_el_tamano_del_producto(tenant):
    """"Conté 3 bolsas de cal" → 75 kg, no 3."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo, contenido_paquete, nombre_paquete) "
                    "VALUES ('Cal', 'Kg', 2000, 0, false, true, 25, 'bolsa') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,0,0)"),
            {"p": pid},
        )
        await s.commit()

        await InventarioService(SqlInventarioRepository(s)).contar(
            producto_id=pid, cantidad_contada=Decimal("3"), unidad="paquete",
            motivo="conteo físico", usuario_id=uid,
        )
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("75.000")
