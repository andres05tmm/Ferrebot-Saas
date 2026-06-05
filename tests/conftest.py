"""Fixtures de prueba: bases efímeras por tenant (creadas, migradas y destruidas por test).

Cada base efímera es una app DB real (Postgres en Docker), lo que permite probar repositorios,
aislamiento entre empresas y migraciones contra Postgres de verdad.
"""
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.db.urls import tenant_url, to_async, to_libpq
from core.tenancy.cache import control_cache
from core.tenancy.capacidades_cache import capacidades_cache
from tools._alembic import upgrade_tenant


@pytest.fixture(autouse=True)
def _reset_caches():
    """Vacía los singletons de caché antes de cada test: las DB efímeras reusan empresa_id/slug y
    el TTL haría que el estado de una prueba se filtrara a la siguiente."""
    capacidades_cache.clear()
    control_cache.clear()
    yield


def _admin():
    return psycopg.connect(to_libpq(get_settings().admin_database_url), autocommit=True)


def create_database(name: str) -> None:
    with _admin() as conn:
        conn.execute(f'CREATE DATABASE "{name}"')


def drop_database(name: str) -> None:
    with _admin() as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@dataclass(slots=True)
class TenantDB:
    name: str
    url: str          # base postgresql://...
    engine: AsyncEngine


@pytest.fixture
async def tenant_factory():
    """Devuelve una corrutina que crea bases efímeras migradas a head; las destruye al final."""
    creados: list[TenantDB] = []

    async def _make() -> TenantDB:
        name = f"test_tenant_{uuid.uuid4().hex[:12]}"
        url = tenant_url(get_settings().tenants_direct_url_base, name)
        create_database(name)
        upgrade_tenant(url)
        engine = create_async_engine(
            to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
        )
        tdb = TenantDB(name=name, url=url, engine=engine)
        creados.append(tdb)
        return tdb

    yield _make

    for tdb in creados:
        await tdb.engine.dispose()
        drop_database(tdb.name)


@pytest.fixture
async def tenant(tenant_factory) -> TenantDB:
    return await tenant_factory()


@pytest.fixture
def seed_producto():
    """Inserta un vendedor + un producto con inventario; devuelve (usuario_id, producto_id)."""
    async def _seed(session: AsyncSession, *, nombre="Martillo", precio="11900", iva=19, stock="100"):
        usuario_id = (
            await session.execute(
                text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id")
            )
        ).scalar_one()
        producto_id = (
            await session.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                    "VALUES (:n,'unidad',:p,:iva,false,true) RETURNING id"
                ),
                {"n": nombre, "p": precio, "iva": iva},
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:pid,:s,0)"),
            {"pid": producto_id, "s": stock},
        )
        await session.commit()
        return usuario_id, producto_id

    return _seed
