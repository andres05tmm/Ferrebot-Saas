"""Tabla y modelo `aliases` (variantes/typos → producto; alimenta búsqueda y bypass).

La tabla la crea la migración tenant 0001 (schema.md); aquí se verifica su forma en head y se prueba
el modelo ORM `Alias` (alias global con producto_id NULL y alias ligado a un producto sembrado).
Corre contra una base efímera real (fixture `tenant`, ya migrada a head).
"""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.inventario.models import Alias

# UNIQUE de una sola columna sobre `termino`.
_UNIQUE_TERMINO = """
SELECT count(*) FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
WHERE rel.relname = 'aliases' AND con.contype = 'u' AND att.attname = 'termino'
"""
# FK de `aliases` hacia `productos`.
_FK_PRODUCTOS = """
SELECT count(*) FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_class frel ON frel.oid = con.confrelid
WHERE rel.relname = 'aliases' AND con.contype = 'f' AND frel.relname = 'productos'
"""
_COLUMNAS = "SELECT column_name FROM information_schema.columns WHERE table_name = 'aliases'"


async def test_aliases_en_head(tenant):
    """head trae la tabla `aliases` con sus columnas, UNIQUE(termino) y FK a productos."""
    async with AsyncSession(tenant.engine) as s:
        columnas = set((await s.execute(text(_COLUMNAS))).scalars().all())
        assert {"id", "termino", "reemplazo", "producto_id", "creado_en", "actualizado_en"} <= columnas
        assert (await s.execute(text(_UNIQUE_TERMINO))).scalar_one() == 1
        assert (await s.execute(text(_FK_PRODUCTOS))).scalar_one() == 1


async def test_modelo_alias_global_y_ligado(tenant, seed_producto):
    """Inserta un alias global (producto_id NULL) y uno ligado a un producto; los lee de vuelta."""
    async with AsyncSession(tenant.engine) as s:
        _usuario_id, producto_id = await seed_producto(s)
        s.add(Alias(termino="drwayll", reemplazo="drywall"))                       # global
        s.add(Alias(termino="martiyo", reemplazo="martillo", producto_id=producto_id))
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        aliases = {a.termino: a for a in (await s.execute(select(Alias))).scalars().all()}

    assert aliases["drwayll"].producto_id is None
    assert aliases["drwayll"].reemplazo == "drywall"
    assert aliases["martiyo"].producto_id == producto_id
    assert aliases["martiyo"].creado_en is not None
