"""Repositorio del control plane: resolver una empresa por slug y descifrar su conexión.

Único lugar que consulta el control DB para tenancy (no SQL suelto en middleware).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.crypto import decrypt
from core.tenancy.context import ResolvedTenant
from core.tenancy.models import Empresa, TenantDatabase


def _resolver(row) -> ResolvedTenant | None:
    """Mapea (Empresa, TenantDatabase) → ResolvedTenant con la URL de conexión descifrada."""
    if row is None:
        return None
    empresa, tdb = row
    connection_url = decrypt(tdb.connection_url_cifrada, get_settings().secrets_master_key)
    return ResolvedTenant(
        id=empresa.id,
        slug=empresa.slug,
        estado=empresa.estado,
        db_name=tdb.db_name,
        connection_url=connection_url,
    )


async def resolve_tenant_by_slug(session: AsyncSession, slug: str) -> ResolvedTenant | None:
    """Devuelve la empresa (con su URL de conexión descifrada) o None si no existe."""
    stmt = (
        select(Empresa, TenantDatabase)
        .join(TenantDatabase, TenantDatabase.empresa_id == Empresa.id)
        .where(Empresa.slug == slug)
    )
    return _resolver((await session.execute(stmt)).first())


async def resolve_tenant_by_id(session: AsyncSession, empresa_id: int) -> ResolvedTenant | None:
    """Resuelve la empresa por id (para jobs del worker, que reciben `tenant_id` explícito)."""
    stmt = (
        select(Empresa, TenantDatabase)
        .join(TenantDatabase, TenantDatabase.empresa_id == Empresa.id)
        .where(Empresa.id == empresa_id)
    )
    return _resolver((await session.execute(stmt)).first())
