"""Búsqueda — capas con SQL contra base efímera: exacta, trigram (pg_trgm) y alias."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.inventario.busqueda import BuscadorProductos
from modules.inventario.repository import SqlInventarioRepository


async def _producto(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES (:n, 'unidad', 1000, 19, false, true) RETURNING id"
            ),
            {"n": nombre},
        )
    ).scalar_one()


async def _buscar(tenant, query):
    async with AsyncSession(tenant.engine) as s:
        return await BuscadorProductos(SqlInventarioRepository(s)).buscar(query)


async def test_exacta_resuelve_sin_sugerencia(tenant):
    async with AsyncSession(tenant.engine) as s:
        pid = await _producto(s, "Cemento Gris")
        await _producto(s, "Drywall")
        await s.commit()

    res = await _buscar(tenant, "cemento gris")   # case-insensitive
    assert res.requiere_confirmacion is False
    assert len(res.coincidencias) == 1
    assert res.coincidencias[0].producto_id == pid
    assert res.coincidencias[0].fuente == "exacta"


async def test_trigram_resuelve_typo(tenant):
    async with AsyncSession(tenant.engine) as s:
        pid = await _producto(s, "Drywall")
        await _producto(s, "Cemento Gris")
        await s.commit()

    # 'drwayll' (del doc) queda en similarity 0.23 < 0.3 en pg_trgm real; usamos un typo
    # que sí cruza el umbral 0.3 (driwall ≈ 0.45). El comportamiento "typo→trigram" es el mismo.
    res = await _buscar(tenant, "driwall")
    assert res.requiere_confirmacion is False
    assert res.coincidencias[0].producto_id == pid
    assert res.coincidencias[0].fuente == "trigram"


async def test_alias_mapea_a_producto(tenant):
    async with AsyncSession(tenant.engine) as s:
        pid = await _producto(s, "Drywall")
        await s.execute(
            text(
                "INSERT INTO aliases (termino, reemplazo, producto_id) "
                "VALUES ('lamina yeso', 'Drywall', :pid)"
            ),
            {"pid": pid},
        )
        await s.commit()

    res = await _buscar(tenant, "lamina yeso")
    assert res.requiere_confirmacion is False
    assert res.coincidencias[0].producto_id == pid
    assert res.coincidencias[0].fuente == "alias"
