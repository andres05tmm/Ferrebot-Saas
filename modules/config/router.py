"""Router de arranque del dashboard: GET /config (api-contract.md).

Devuelve `{ features, branding, usuario }` para que el dashboard se configure al cargar. Es el
bootstrap: NO lleva feature-gate (rol mínimo = cualquier usuario autenticado). Las capacidades vienen
cacheadas (`get_capacidades`) y el branding del control DB; ambas deps son inyectables para test.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import control_session
from core.tenancy.catalogo import capacidades_completas
from core.tenancy.control_repo import leer_branding

router = APIRouter(tags=["config"])


class Branding(BaseModel):
    """Marca blanca de la empresa (control DB); `color_primario` siempre presente (default de marca)."""

    logo_url: str | None = None
    color_primario: str
    nombre_comercial: str | None = None
    dominio: str | None = None


class Usuario(BaseModel):
    """Identidad del usuario autenticado, derivada del `Principal` del token."""

    id: int
    rol: str
    tenant: str


class ConfigLeer(BaseModel):
    """Respuesta de GET /config: capacidades activas, branding y usuario."""

    features: list[str]
    branding: Branding
    usuario: Usuario


async def get_branding(request: Request) -> Branding:
    """Lee el branding de la empresa del request desde el control DB (defaults si no hay fila)."""
    async with control_session() as cs:
        datos = await leer_branding(cs, request.state.tenant.id)
    return Branding(**datos)


@router.get("/config", response_model=ConfigLeer)
async def obtener_config(
    user: Principal = Depends(get_current_user),
    capacidades: frozenset[str] = Depends(get_capacidades),
    branding: Branding = Depends(get_branding),
) -> ConfigLeer:
    """Arranque del dashboard: núcleo ∪ efectivas (ordenado), branding y usuario autenticado."""
    return ConfigLeer(
        features=sorted(capacidades_completas(capacidades)),
        branding=branding,
        usuario=Usuario(id=user.user_id, rol=user.rol, tenant=user.tenant),
    )
