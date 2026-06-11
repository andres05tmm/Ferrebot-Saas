"""Repositorio del control plane: resolver una empresa por slug y descifrar su conexión.

Único lugar que consulta el control DB para tenancy (no SQL suelto en middleware).
"""
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.crypto import decrypt
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.context import ResolvedTenant
from core.tenancy.models import Empresa, TenantDatabase, WaNumero

# Branding por defecto cuando la empresa no tiene fila en `branding` (color de marca FerreBot).
# `tema` None → el dashboard usa el tema base (rojo); un tenant lo declara explícito (p. ej. "aurora").
_BRANDING_DEFAULT: dict[str, str | None] = {
    "logo_url": None, "color_primario": "#C8200E", "nombre_comercial": None,
    "dominio": None, "tema": None,
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
        nombre=empresa.nombre,
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


async def resolve_tenant_by_wa_number(
    session: AsyncSession, phone_number_id: str
) -> ResolvedTenant | None:
    """Resuelve la empresa por el `phone_number_id` del canal de WhatsApp (Kapso → wa_numeros).

    Solo mapeos activos (`wa_numeros.estado = 'activo'`). None si el número no está mapeado.
    """
    stmt = (
        select(Empresa, TenantDatabase)
        .join(TenantDatabase, TenantDatabase.empresa_id == Empresa.id)
        .join(WaNumero, WaNumero.empresa_id == Empresa.id)
        .where(WaNumero.phone_number_id == phone_number_id, WaNumero.estado == "activo")
    )
    return _resolver((await session.execute(stmt)).first())


async def listar_wa_numeros_activos(session: AsyncSession) -> list[tuple[int, str]]:
    """`(empresa_id, phone_number_id)` de cada canal de WhatsApp activo (para jobs multi-tenant).

    Solo las empresas con un número activo pueden recibir/enviar por WhatsApp; el job de recordatorios
    itera sobre estas y, por tenant, verifica el flag `pack_agenda` antes de procesar.
    """
    stmt = (
        select(WaNumero.empresa_id, WaNumero.phone_number_id)
        .where(WaNumero.estado == "activo")
        .order_by(WaNumero.empresa_id)
    )
    return [(int(eid), str(pn)) for eid, pn in (await session.execute(stmt)).all()]


@dataclass(frozen=True, slots=True)
class TenantResumen:
    """Vista del panel super-admin de una empresa (ADR 0010): solo lectura del control DB."""

    id: int
    slug: str
    nombre: str
    estado: str
    plan: str | None
    features: tuple[str, ...]          # features EFECTIVAS (plan ± overrides), ordenadas
    wa_numero: str | None              # phone_number_id del canal WhatsApp activo, si tiene


async def listar_tenants(session: AsyncSession) -> list[TenantResumen]:
    """Lista las empresas para el panel super-admin: slug, nombre, estado, plan, features efectivas y
    su número de WhatsApp activo (si tiene). Lectura del CONTROL DB; el super-admin nunca abre la base
    de un tenant (ADR 0010 §D2). Lista pequeña (un puñado de tenants) → sin paginación (performance.md)."""
    rows = (
        await session.execute(
            text(
                "SELECT e.id, e.slug, e.nombre, e.estado, p.nombre AS plan, w.phone_number_id AS wa_numero "
                "FROM empresas e "
                "LEFT JOIN planes p ON p.id = e.plan_id "
                "LEFT JOIN wa_numeros w ON w.empresa_id = e.id AND w.estado = 'activo' "
                "ORDER BY e.slug"
            )
        )
    ).all()
    capacidades = ControlCapacidades(session)
    resumenes: list[TenantResumen] = []
    for r in rows:
        features = await capacidades.efectivas(r.id)
        resumenes.append(
            TenantResumen(
                id=int(r.id), slug=r.slug, nombre=r.nombre, estado=r.estado, plan=r.plan,
                features=tuple(sorted(features)), wa_numero=r.wa_numero,
            )
        )
    return resumenes


async def leer_branding(session: AsyncSession, empresa_id: int) -> dict[str, str | None]:
    """Branding (logo, color, nombre comercial, dominio) de la empresa; defaults si no hay fila."""
    row = (
        await session.execute(
            text(
                "SELECT logo_url, color_primario, nombre_comercial, dominio, tema "
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
        "tema": row[4],
    }
