"""Migración tenant 0006 — rediseño de producto (upgrade/downgrade limpios).

`productos.precio_mayorista`→`precio_especial` (rename) y `marca`→`proveedor_id` (FK a proveedores,
ON DELETE SET NULL). Corre contra una base efímera real (fixture `tenant`, ya en head).
"""
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant


async def _columnas(engine, tabla: str) -> set[str]:
    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda c: inspect(c).get_columns(tabla))
    return {col["name"] for col in cols}


async def _fks(engine, tabla: str) -> list[dict]:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: inspect(c).get_foreign_keys(tabla))


async def test_0006_producto_proveedor_en_head(tenant):
    # head (incluye 0006): proveedor_id + precio_especial; ya NO marca ni precio_mayorista.
    cols = await _columnas(tenant.engine, "productos")
    assert {"proveedor_id", "precio_especial"} <= cols
    assert "marca" not in cols and "precio_mayorista" not in cols
    # La FK a proveedores existe.
    fks = await _fks(tenant.engine, "productos")
    assert any(fk["referred_table"] == "proveedores" and fk["constrained_columns"] == ["proveedor_id"]
               for fk in fks)

    # La FK pone NULL al borrar el proveedor (ON DELETE SET NULL): no deja productos huérfanos.
    async with AsyncSession(tenant.engine) as s:
        prov = (await s.execute(
            text("INSERT INTO proveedores (nombre) VALUES ('Tmp') RETURNING id"))).scalar_one()
        pid = (await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
                "activo, proveedor_id) VALUES ('P','u',1,0,false,true,:pr) RETURNING id"
            ),
            {"pr": prov},
        )).scalar_one()
        await s.execute(text("DELETE FROM proveedores WHERE id=:i"), {"i": prov})
        await s.commit()
        quedo = (await s.execute(
            text("SELECT proveedor_id FROM productos WHERE id=:p"), {"p": pid})).scalar_one()
        assert quedo is None

    # downgrade a 0005 → vuelve marca/precio_mayorista; se va proveedor_id/precio_especial.
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0005_drop_config_empresa")
    cols = await _columnas(tenant.engine, "productos")
    assert {"marca", "precio_mayorista"} <= cols
    assert "proveedor_id" not in cols and "precio_especial" not in cols

    # upgrade vuelve a head sin romper.
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    cols = await _columnas(tenant.engine, "productos")
    assert {"proveedor_id", "precio_especial"} <= cols
