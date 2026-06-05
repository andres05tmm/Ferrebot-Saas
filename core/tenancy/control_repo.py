"""Repositorio del control plane: resolver una empresa por slug y descifrar su conexión.

Único lugar que consulta el control DB para tenancy (no SQL suelto en middleware).
"""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.crypto import decrypt
from core.tenancy.context import ResolvedTenant
from core.tenancy.models import Empresa, TenantDatabase

# Branding por defecto cuando la empresa no tiene fila en `branding` (color de marca FerreBot).
_BRANDING_DEFAULT: dict[str, str | None] = {
    "logo_url": None, "color_primario": "#C8200E", "nombre_comercial": None, "dominio": None,
}


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


async def leer_branding(session: AsyncSession, empresa_id: int) -> dict[str, str | None]:
    """Branding (logo, color, nombre comercial, dominio) de la empresa; defaults si no hay fila."""
    row = (
        await session.execute(
            text(
                "SELECT logo_url, color_primario, nombre_comercial, dominio "
                "FROM branding WHERE empresa_id = :e"
            ),
            {"e": empresa_id},
        )
    ).first()
    if row is None:
        return dict(_BRANDING_DEFAULT)
    return {
        "logo_url": row[0],
        "color_primario": row[1] or _BRANDING_DEFAULT["color_primario"],
        "nombre_comercial": row[2],
        "dominio": row[3],
    }
