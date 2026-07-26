"""Migración 0067: corrección de compras + procedencia de cada pago — up/down/up limpio.

Base efímera PG. Verifica el contrato: las columnas existen tras `head`, el `downgrade` a 0066 las
retira y re-aplicar `head` las deja igual. Todas son aditivas y nullable (o con default), así que el
histórico de producción no cambia de comportamiento: una compra vieja tiene `correcciones = 0` y una
compra/abono viejos no dicen de dónde salió la plata (no se puede inventar).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_COLS = (
    "SELECT count(*) FROM information_schema.columns WHERE "
    "(table_name='compras' AND column_name IN ('corregida_en','correcciones','nota_correccion',"
    "'ultima_correccion_key','factura_proveedor_id')) OR "
    "(table_name='pedidos_proveedor' AND column_name IN ('origen_anticipo','origen_pago')) OR "
    "(table_name='facturas_abonos' AND column_name IN ('origen_fondos','caja_movimiento_id'))"
)


async def test_0067_up_down_up_limpio(tenant):
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COLS))).scalar_one() == 9
        # `correcciones` arranca en 0 sin tocar nada (server_default): el histórico queda coherente.
        await s.execute(text(
            "INSERT INTO compras (proveedor_id, total) VALUES (NULL, 1000)"
        ))
        await s.commit()
        assert (await s.execute(text(
            "SELECT correcciones FROM compras ORDER BY id DESC LIMIT 1"
        ))).scalar_one() == 0

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0066_cliente_tipo_persona")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COLS))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COLS))).scalar_one() == 9
