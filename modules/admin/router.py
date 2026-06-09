"""Panel super-admin: API de plataforma (ADR 0010 §D2). Opera CROSS-TENANT sobre el control DB.

Todas las rutas van bajo `/api/v1/admin`, **exentas del TenantMiddleware** (no son por-empresa; ver
core/tenancy/middleware) y **gateadas por `require_role("super_admin")`**: solo una identidad de
PLATAFORMA (JWT con `scope=platform`, sin `tenant`) entra; un admin/vendedor de tenant → 403. El
super-admin lee el control DB; nunca abre la base de un tenant directamente.

B1 entrega solo la LECTURA (listar tenants); crear/togglear/identidades llegan en B2–B4.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.auth import require_platform
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.control_repo import listar_tenants

log = get_logger("admin")

# Gate de plataforma a nivel de router: cada ruta exige una identidad de plataforma (super_admin +
# scope=platform). Cubre toda /admin/* (ADR 0010 §D2).
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_platform)])


class TenantOut(BaseModel):
    """Una empresa vista desde el panel super-admin (control DB)."""

    id: int
    slug: str
    nombre: str
    estado: str
    plan: str | None = None
    features: list[str] = []
    wa_numero: str | None = None


@router.get("/tenants", response_model=list[TenantOut])
async def get_tenants() -> list[TenantOut]:
    """Lista las empresas de la plataforma (slug, nombre, estado, plan, features efectivas, número WA).

    El gate `require_platform` está a nivel de router (cubre toda /admin/*)."""
    async with control_session() as cs:
        resumenes = await listar_tenants(cs)
    return [
        TenantOut(
            id=t.id, slug=t.slug, nombre=t.nombre, estado=t.estado, plan=t.plan,
            features=list(t.features), wa_numero=t.wa_numero,
        )
        for t in resumenes
    ]
