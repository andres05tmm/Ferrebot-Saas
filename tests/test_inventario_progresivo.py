"""Armar el inventario producto por producto, empezando por lo que más rota (issue #180).

La ferretería no lleva stock de nada y no va a cerrar el local a contar 600 productos. El inventario
se construye con el trabajo del día, y el ORDEN es lo que decide si eso sirve: con los 40 productos
que más se venden ya se controla la mitad del negocio; empezando por la A se cuadran tornillos que
rotan dos veces al año.

La maquinaria es de 0052 (`inventario.cuadrado_at`); acá se prueba la cola de pendientes y que
cuadrar de verdad los saque de ella.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService


async def _producto(s: AsyncSession, nombre: str, *, stock: str = "0") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
                "activo) VALUES (:n,'Unidad',1000,19,false,true) RETURNING id"
            ),
            {"n": nombre},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


async def _vender(s: AsyncSession, pid: int, veces: int) -> None:
    """Ventas mínimas para dar rotación (el orden de la cola sale de contar líneas)."""
    vendedor = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id")
        )
    ).scalar_one()
    for _ in range(veces):
        venta_id = (
            await s.execute(
                text(
                    "INSERT INTO ventas (consecutivo, vendedor_id, subtotal, impuestos, total, "
                    "metodo_pago, estado, origen) VALUES "
                    "(nextval('ventas_consecutivo_seq'),:u,1000,0,1000,"
                    "'efectivo','completada','web') RETURNING id"
                ),
                {"u": vendedor},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO ventas_detalle (venta_id, producto_id, descripcion, cantidad, "
                "precio_unitario, iva) VALUES (:v,:p,'x',1,1000,19)"
            ),
            {"v": venta_id, "p": pid},
        )


async def test_la_cola_pone_primero_lo_que_mas_se_vende(tenant):
    """Con 3 pendientes, el que más se tocó en el mostrador va de primero."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        poco = await _producto(s, "Alicate raro")
        mucho = await _producto(s, "Cemento Gris")
        nada = await _producto(s, "Bisagra olvidada")
        await _vender(s, poco, 1)
        await _vender(s, mucho, 5)
        await s.commit()

        cola = await SqlInventarioRepository(s).por_cuadrar(dias=60, limite=10)

    ids = [c["producto_id"] for c in cola]
    assert ids[:2] == [mucho, poco]
    assert nada in ids                       # el que nunca se ha vendido va al final, no se pierde
    assert cola[0]["lineas_vendidas"] == 5


async def test_cuadrar_saca_al_producto_de_la_cola(tenant):
    """"Se acabó" es un conteo en 0: fija el stock y sella el producto como confiable."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s, "Cemento Gris", stock="-12")   # ventas anotadas sin haber contado
        await s.commit()
        repo = SqlInventarioRepository(s)

        assert [c["producto_id"] for c in await repo.por_cuadrar(dias=60, limite=10)] == [pid]

        await InventarioService(repo).contar(
            producto_id=pid, cantidad_contada=Decimal("0"), motivo="Se acabó", usuario_id=None,
        )
        await s.commit()

        assert await repo.por_cuadrar(dias=60, limite=10) == []
        assert await repo.conteo_cuadrados() == (1, 1)

    async with AsyncSession(tenant.engine) as s:
        stock = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()
    assert stock == Decimal("0.000")          # el negativo del backlog quedó saldado


async def test_el_pendiente_muestra_su_negativo_sin_tratarlo_como_error(tenant):
    """El stock negativo de un producto sin cuadrar es backlog, no una alarma: viaja en la cola."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s, "Yeso", stock="-40")
        await s.commit()

        cola = await SqlInventarioRepository(s).por_cuadrar(dias=60, limite=10)

    assert cola[0]["producto_id"] == pid
    assert cola[0]["stock_actual"] == Decimal("-40.000")


async def test_un_producto_inactivo_no_entra_a_la_cola(tenant):
    """Los duplicados que se inactivaron al cuadrar el catálogo no son trabajo pendiente."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s, "TORNILLOS HEX 3/8X2 GALBANIZADO")
        await s.execute(text("UPDATE productos SET activo=false WHERE id=:p"), {"p": pid})
        await s.commit()

        repo = SqlInventarioRepository(s)
        assert await repo.por_cuadrar(dias=60, limite=10) == []
        assert await repo.conteo_cuadrados() == (0, 0)
