"""El backfill de 0072 solo toca el granel — regresión de un daño REAL en producción.

`precio_paquete` nació para separar el precio del bulto del precio del kilo. El backfill tenía que
mover a la columna nueva los productos donde `precio_venta` significaba el EMPAQUE: los granel
(GRM/Cms/MLT), los únicos donde el motor de precios dividía por una convención del código.

La primera versión usó `contenido_paquete IS NOT NULL` como señal, y eso estaba mal: ese campo es de
0069 y el dueño ya lo había llenado desde el dashboard en nueve polvos por kilo —cuyo `precio_venta`
YA era por kilo—. La migración se los dividió por 25 y dejó el cemento a $60 el kilo en producción.

La señal correcta es la unidad de medida, no la presencia del empaque.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant


async def _productos(engine) -> dict[str, tuple[Decimal, Decimal | None]]:
    async with AsyncSession(engine) as s:
        filas = (
            await s.execute(text("SELECT nombre, precio_venta, precio_paquete FROM productos"))
        ).all()
    return {f.nombre: (f.precio_venta, f.precio_paquete) for f in filas}


async def test_backfill_divide_el_granel_y_no_toca_el_kilo(tenant):
    """Un producto por kilo con bolsa de 25 kg conserva su precio; la caja de puntilla se separa."""
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0071_gastos_tipo_y_recurrentes")

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        # El caso que rompió prod: el dueño puso el tamaño de la bolsa, pero el precio es POR KILO.
        await s.execute(text(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, contenido_paquete, "
            "nombre_paquete, iva, permite_fraccion, activo) "
            "VALUES ('Cemento Gris','Kg',1500,25,'bolsa',19,false,true)"
        ))
        # Granel de verdad: aquí `precio_venta` SÍ es el precio de la caja de 500 g.
        await s.execute(text(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, contenido_paquete, "
            "nombre_paquete, iva, permite_fraccion, activo) "
            "VALUES ('Puntilla 1\"','GRM',7500,500,'caja',19,false,true)"
        ))
        # Sin empaque: no se toca de ninguna manera.
        await s.execute(text(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
            "activo) VALUES ('Martillo','Unidad',11900,19,false,true)"
        ))
        await s.commit()

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url, "0072_precio_paquete")

    productos = await _productos(tenant.engine)
    assert productos["Cemento Gris"] == (Decimal("1500.00"), None)      # intacto: el kilo es el kilo
    assert productos['Puntilla 1"'] == (Decimal("15.00"), Decimal("7500.00"))
    assert productos["Martillo"] == (Decimal("11900.00"), None)
