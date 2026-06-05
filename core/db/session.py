"""Dependencias de sesión: tenant (negocio) y control.

`get_tenant_db` ata la sesión a la base de la empresa del request por todo su ciclo de vida.
Nunca se cambia de tenant a mitad de flujo (regla de multitenancy #2).
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from core.config import get_settings
from core.db.engine_cache import engine_cache
from core.db.urls import to_async
from core.tenancy.context import ResolvedTenant

_control_engine: AsyncEngine | None = None
_control_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _control() -> async_sessionmaker[AsyncSession]:
    global _control_engine, _control_sessionmaker
    if _control_sessionmaker is None:
        url = to_async(get_settings().control_database_url)
        _control_engine = create_async_engine(
            url, pool_size=2, max_overflow=2, pool_pre_ping=True,
            connect_args={"statement_cache_size": 0},
        )
        _control_sessionmaker = async_sessionmaker(_control_engine, expire_on_commit=False)
    return _control_sessionmaker


async def get_control_db() -> AsyncIterator[AsyncSession]:
    """Sesión del control DB (plano de control: empresas, planes, secretos)."""
    async with _control()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def control_session() -> AsyncIterator[AsyncSession]:
    """Sesión del control DB como context manager (espejo de `tenant_session`, para el wiring del
    bot: cada wrapper abre una sesión de control FRESCA por llamada, con commit/rollback)."""
    async with _control()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def tenant_session(tenant: ResolvedTenant) -> AsyncIterator[AsyncSession]:
    """Crea una sesión atada a la base de `tenant` (uso fuera de FastAPI: jobs, tests, bot)."""
    engine = await engine_cache.get_or_create(tenant.id, tenant.async_url)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_tenant_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: sesión de la empresa resuelta por TenantMiddleware.

    `request` DEBE estar anotado `Request`: sin la anotación FastAPI lo trata como query param en
    todos los endpoints que dependen de esto (lo destapó el smoke E2E de facturación).
    """
    tenant: ResolvedTenant = request.state.tenant
    async for session in tenant_session(tenant):
        yield session
