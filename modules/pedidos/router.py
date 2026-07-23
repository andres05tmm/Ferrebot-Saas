"""Router del pack pedidos (backend del kanban del dashboard). Gateado por `pack_pedidos`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: el kanban (listar/avanzar
estados) es de **staff** (vendedor+ — es la operación del restaurante); config y zonas de **admin**.
La lógica vive en `PedidosService`; aquí solo se valida, se mapea a HTTP y se serializa — sin SQL.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.facturacion.pos_hook import encolar_cierre_pos
from modules.pedidos.conversion import PedidoNoConvertible, convertir_pedido
from modules.pedidos.errors import PedidoInexistente, TransicionInvalida
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.schemas import (
    CambioEstado,
    ConversionLeer,
    ConvertirPayload,
    PedidoConfigActualizar,
    PedidoConfigLeer,
    PedidoLeer,
    ZonaCrear,
    ZonaLeer,
)
from modules.pedidos.service import PedidosService
from modules.ventas.errors import IdempotenciaConflicto, StockInsuficiente
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.router import get_control_stock_estricto
from modules.ventas.service import VentaService

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


@router.post(
    "/{pedido_id}/convertir",
    response_model=ConversionLeer,
    status_code=status.HTTP_201_CREATED,
    # ADR 0032 (F1): la conversión crea una VENTA → exige además la feature fina `ventas`.
    dependencies=[Depends(require_feature("ventas"))],
)
async def convertir_en_venta(
    pedido_id: int,
    payload: ConvertirPayload,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    control_stock_estricto: bool = Depends(get_control_stock_estricto),
) -> ConversionLeer:
    """Convierte el pedido en venta (snapshot + vínculo idempotente, misma transacción).

    Reintento/ya convertido → 200 con `replay=true` (misma venta). Respeta el control de stock
    estricto del tenant (misma dependencia que POST /ventas). Tras la conversión se encola el
    cierre fiscal según capacidades del tenant (best-effort: nunca rompe la venta), como en POST /ventas.
    """
    try:
        res = await convertir_pedido(
            pedido_id,
            repo=SqlPedidosRepository(session),
            ventas=VentaService(SqlVentasRepository(session)),
            usuario_id=user.user_id,
            metodo_pago=payload.metodo_pago,
            control_stock_estricto=control_stock_estricto,
        )
    except PedidoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pedido no encontrado") from exc
    except PedidoNoConvertible as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except IdempotenciaConflicto as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except StockInsuficiente as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    else:
        await encolar_cierre_pos(request, session, res.venta_id)
    return ConversionLeer(venta_id=res.venta_id, total=res.total, replay=res.replay)


@router.get("/resumen-dia")
async def resumen_dia(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> dict:
    """Resumen del día restaurantero (F7 / ADR 0032): canales, top platos y ciclo medio. Solo lectura."""
    resumen = await SqlPedidosRepository(session).resumen_dia()
    return {
        "canales": [
            {**c, "vendido": str(c["vendido"])} for c in resumen["canales"]
        ],
        "top_platos": [
            {**t, "unidades": str(t["unidades"]), "total": str(t["total"])}
            for t in resumen["top_platos"]
        ],
        "ciclo_medio_min": resumen["ciclo_medio_min"],
    }


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
