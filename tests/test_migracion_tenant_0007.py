"""Migración tenant 0007 — `metodo_pago += 'datafono'` (upgrade/downgrade limpios).

Verifica contra una base efímera real (fixture `tenant`, ya en head) que el valor 'datafono' está
en el enum `metodo_pago`, que el downgrade a 0006 lo quita recreando el tipo, y que el upgrade lo
vuelve a poner. El ADD VALUE corre en `autocommit_block` (gotcha: no admite transacción)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_LABELS = (
    "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
    "WHERE t.typname = 'metodo_pago' ORDER BY e.enumsortorder"
)


async def _metodos(engine) -> list[str]:
    async with AsyncSession(engine) as s:
        return [r[0] for r in (await s.execute(text(_LABELS))).all()]


async def test_0007_datafono_en_head(tenant):
    # head (incluye 0007): el enum tiene 'datafono' (además de los 6 originales).
    metodos = await _metodos(tenant.engine)
    assert "datafono" in metodos
    assert {"efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado"} <= set(metodos)

    # downgrade a 0006 → se recrea el tipo SIN 'datafono'; los originales siguen.
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0006_producto_proveedor")
    metodos = await _metodos(tenant.engine)
    assert "datafono" not in metodos
    assert {"efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado"} == set(metodos)

    # upgrade vuelve a head sin romper → 'datafono' otra vez.
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    assert "datafono" in await _metodos(tenant.engine)


async def test_0007_venta_acepta_datafono_en_head(tenant):
    # La columna real `ventas.metodo_pago` acepta el nuevo valor (no solo el tipo en abstracto).
    async with AsyncSession(tenant.engine) as s:
        # cast directo al enum: falla si 'datafono' no es un valor válido del tipo.
        valor = (await s.execute(text("SELECT 'datafono'::metodo_pago"))).scalar_one()
        assert valor == "datafono"
