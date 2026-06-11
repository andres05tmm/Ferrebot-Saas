"""Router del pack pedidos (backend del kanban del dashboard). Gateado por `pack_pedidos`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: el kanban (listar/avanzar
estados) es de **staff** (vendedor+ — es la operación del restaurante); config y zonas de **admin**.
La lógica vive en `PedidosService`; aquí solo se valida, se mapea a HTTP y se serializa — sin SQL.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.pedidos.errors import PedidoInexistente, TransicionInvalida
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.schemas import (
    CambioEstado,
    PedidoConfigActualizar,
    PedidoConfigLeer,
    PedidoLeer,
    ZonaCrear,
    ZonaLeer,
)
from modules.pedidos.service import PedidosService

# Todo el router exige el flag pack_pedidos (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/pedidos", tags=["pedidos"],
    dependencies=[Depends(require_feature("pack_pedidos"))],
)


def get_pedidos_service(session: AsyncSession = Depends(get_tenant_db)) -> PedidosService:
    """Arma el `PedidosService` sobre la sesión del tenant (los tests lo overridean)."""
    return PedidosService(SqlPedidosRepository(session))


@router.get("", response_model=list[PedidoLeer])
async def listar_pedidos(
    estado: list[str] | None = Query(default=None),
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[PedidoLeer]:
    return await service.listar(estados=estado)


@router.put("/{pedido_id}/estado", response_model=PedidoLeer)
async def cambiar_estado(
    pedido_id: int,
    payload: CambioEstado,
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> PedidoLeer:
    try:
        return await service.cambiar_estado(pedido_id, payload.estado)
    except PedidoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pedido no encontrado") from exc
    except TransicionInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Transición inválida: {exc}") from exc


@router.get("/config", response_model=PedidoConfigLeer)
async def obtener_config(
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("admin")),
) -> PedidoConfigLeer:
    return await service.obtener_config()


@router.put("/config", response_model=PedidoConfigLeer)
async def actualizar_config(
    payload: PedidoConfigActualizar,
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("admin")),
) -> PedidoConfigLeer:
    return await service.guardar_config(payload)


@router.get("/zonas", response_model=list[ZonaLeer])
async def listar_zonas(
    incluir_inactivas: bool = Query(default=False),
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ZonaLeer]:
    return await service.listar_zonas(solo_activas=not incluir_inactivas)


@router.post("/zonas", response_model=ZonaLeer, status_code=status.HTTP_201_CREATED)
async def crear_zona(
    payload: ZonaCrear,
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("admin")),
) -> ZonaLeer:
    return await service.crear_zona(payload)


@router.delete("/zonas/{zona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desactivar_zona(
    zona_id: int,
    service: PedidosService = Depends(get_pedidos_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    await service.desactivar_zona(zona_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
