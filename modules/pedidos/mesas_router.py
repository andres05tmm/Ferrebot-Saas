"""Router del salón (F3 Pack Restaurante, ADR 0032 D4). Gateado por `pack_mesas` (404 sin él).

RBAC: operar el salón (abrir/agregar/precuenta/cobrar) es de **staff** (vendedor+); el CRUD de
mesas es de **admin**. El cobro crea una VENTA → exige además la feature fina `ventas` y respeta el
control de stock estricto del tenant (misma dependencia que POST /ventas). Sin SQL aquí.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.facturacion.pos_hook import encolar_cierre_pos
from modules.pedidos.conversion import PedidoNoConvertible
from modules.pedidos.errors import (
    ModificadorInvalido,
    ModificadorNoEncontrado,
    ProductoNoEncontrado,
    StockInsuficiente,
)
from modules.pedidos.mesas import MesaInexistente, MesaSinOrden, MesasService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.schemas import (
    CobrarMesa,
    ConversionLeer,
    MesaCrear,
    MesaLeer,
    PedidoLeer,
    RondaMesa,
)
from modules.pedidos.service import ItemPedido
from modules.ventas.errors import IdempotenciaConflicto
from modules.ventas.errors import StockInsuficiente as VentaStockInsuficiente
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.router import get_control_stock_estricto
from modules.ventas.service import VentaService

router = APIRouter(
    prefix="/mesas", tags=["mesas"],
    dependencies=[Depends(require_feature("pack_mesas"))],
)


def get_mesas_service(session: AsyncSession = Depends(get_tenant_db)) -> MesasService:
    return MesasService(SqlPedidosRepository(session))


def _404_mesa(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada")


@router.get("", response_model=list[MesaLeer])
async def listar_mesas(
    service: MesasService = Depends(get_mesas_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[MesaLeer]:
    filas = await service.listar()
    return [
        MesaLeer(
            id=m.id, nombre=m.nombre, zona=m.zona, activo=m.activo,
            pedido_id=orden.id if orden else None,
            total=orden.total if orden else None,
        )
        for m, orden in filas
    ]


@router.post("", response_model=MesaLeer, status_code=status.HTTP_201_CREATED)
async def crear_mesa(
    payload: MesaCrear,
    service: MesasService = Depends(get_mesas_service),
    _user: Principal = Depends(require_role("admin")),
) -> MesaLeer:
    mesa = await service.crear(nombre=payload.nombre, zona=payload.zona)
    return MesaLeer(id=mesa.id, nombre=mesa.nombre, zona=mesa.zona, activo=mesa.activo)


@router.delete("/{mesa_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desactivar_mesa(
    mesa_id: int,
    service: MesasService = Depends(get_mesas_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    await service.desactivar(mesa_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{mesa_id}/abrir", response_model=PedidoLeer, status_code=status.HTTP_201_CREATED)
async def abrir_mesa(
    mesa_id: int,
    service: MesasService = Depends(get_mesas_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> PedidoLeer:
    try:
        return await service.abrir(mesa_id)
    except MesaInexistente as exc:
        raise _404_mesa(exc) from exc


@router.post("/{mesa_id}/items", response_model=PedidoLeer)
async def agregar_ronda(
    mesa_id: int,
    payload: RondaMesa,
    service: MesasService = Depends(get_mesas_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> PedidoLeer:
    items = [
        ItemPedido(
            producto=i.producto, cantidad=i.cantidad,
            modificadores=tuple(m.strip() for m in i.modificadores if m.strip()),
        )
        for i in payload.items
    ]
    try:
        return await service.agregar(mesa_id, items)
    except MesaInexistente as exc:
        raise _404_mesa(exc) from exc
    except MesaSinOrden as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "La mesa no tiene una orden abierta") from exc
    except ProductoNoEncontrado as exc:
        detalle = f"No existe '{exc.nombre}' en el catálogo"
        if exc.sugerencias:
            detalle += f" (¿{', '.join(exc.sugerencias)}?)"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detalle) from exc
    except (ModificadorNoEncontrado, ModificadorInvalido, StockInsuficiente) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/{mesa_id}/precuenta", response_model=PedidoLeer)
async def precuenta(
    mesa_id: int,
    service: MesasService = Depends(get_mesas_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> PedidoLeer:
    try:
        return await service.precuenta(mesa_id)
    except MesaSinOrden as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "La mesa no tiene una orden abierta") from exc


@router.post(
    "/{mesa_id}/cobrar",
    response_model=ConversionLeer,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("ventas"))],
)
async def cobrar_mesa(
    mesa_id: int,
    payload: CobrarMesa,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    control_stock_estricto: bool = Depends(get_control_stock_estricto),
) -> ConversionLeer:
    """Cobra la mesa: venta idempotente por el puente F1 + propina opcional + mesa liberada."""
    try:
        res = await MesasService(SqlPedidosRepository(session)).cobrar(
            mesa_id,
            ventas=VentaService(SqlVentasRepository(session)),
            usuario_id=user.user_id,
            metodo_pago=payload.metodo_pago,
            propina=payload.propina,
            control_stock_estricto=control_stock_estricto,
        )
    except MesaSinOrden as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "La mesa no tiene una orden abierta") from exc
    except (PedidoNoConvertible, IdempotenciaConflicto, VentaStockInsuficiente) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    else:
        await encolar_cierre_pos(request, session, res.venta_id)
    return ConversionLeer(venta_id=res.venta_id, total=res.total, replay=res.replay)
