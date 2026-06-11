"""Router del pack postventa (dashboard). Gateado por `pack_postventa`.

Sin el flag, 404. RBAC: todo admin (la configuración y las respuestas con teléfono son del dueño).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.postventa.repository import SqlPostventaRepository
from modules.postventa.schemas import (
    PostventaConfigActualizar,
    PostventaConfigLeer,
    RespuestaLeer,
)
from modules.postventa.service import PostventaService

router = APIRouter(
    prefix="/postventa", tags=["postventa"],
    dependencies=[Depends(require_feature("pack_postventa"))],
)


def get_postventa_service(session: AsyncSession = Depends(get_tenant_db)) -> PostventaService:
    """Arma el `PostventaService` sobre la sesión del tenant (los tests lo overridean)."""
    return PostventaService(SqlPostventaRepository(session))


@router.get("/config", response_model=PostventaConfigLeer)
async def obtener_config(
    service: PostventaService = Depends(get_postventa_service),
    _user: Principal = Depends(require_role("admin")),
) -> PostventaConfigLeer:
    return await service.obtener_config()


@router.put("/config", response_model=PostventaConfigLeer)
async def actualizar_config(
    payload: PostventaConfigActualizar,
    service: PostventaService = Depends(get_postventa_service),
    _user: Principal = Depends(require_role("admin")),
) -> PostventaConfigLeer:
    config = await service.obtener_config()
    for campo, valor in payload.model_dump().items():
        setattr(config, campo, valor)
    return config


@router.get("/respuestas", response_model=list[RespuestaLeer])
async def listar_respuestas(
    service: PostventaService = Depends(get_postventa_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[RespuestaLeer]:
    return await service.listar_respuestas()


@router.get("/satisfaccion")
async def satisfaccion(
    service: PostventaService = Depends(get_postventa_service),
    _user: Principal = Depends(require_role("admin")),
) -> dict:
    """KPI del dueño (M-agente del plan): promedio 1-5 y nº de respuestas."""
    return await service.satisfaccion()
