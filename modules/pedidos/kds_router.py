"""Router del KDS (F4 Pack Restaurante, ADR 0032 D5). Gateado por `kds` (404 sin él).

RBAC: operar la cocina (ver cola, avanzar comandas) es de **staff**; zonas y ruteo producto→zona
son de **admin**. Sin SQL aquí: compone las comandas con sus ítems de pedido (una consulta, sin N+1).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.pedidos.kds import ComandaInexistente, KdsService, TransicionComandaInvalida
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.schemas import (
    CambioEstadoComanda,
    ComandaItemLeer,
    ComandaLeer,
    KdsLeer,
    RuteoComanda,
    ZonaComandaCrear,
)

router = APIRouter(
    prefix="/kds", tags=["kds"],
    dependencies=[Depends(require_feature("kds"))],
)


def get_kds_service(session: AsyncSession = Depends(get_tenant_db)) -> KdsService:
    return KdsService(SqlPedidosRepository(session))


async def _componer(comandas, repo: SqlPedidosRepository, zonas) -> list[ComandaLeer]:
    nombres_zona = {z.id: z.nombre for z in zonas}
    ids = [ci.pedido_item_id for c in comandas for ci in c.items]
    items = await repo.pedido_items_por_ids(ids)
    return [
        ComandaLeer(
            id=c.id, pedido_id=c.pedido_id, zona_id=c.zona_id,
            zona=nombres_zona.get(c.zona_id, "cocina" if c.zona_id is None else None),
            estado=c.estado, creada_en=c.creada_en,
            items=[
                ComandaItemLeer(
                    nombre=items[ci.pedido_item_id].nombre if ci.pedido_item_id in items else "?",
                    cantidad=ci.cantidad,
                    modificadores=(
                        items[ci.pedido_item_id].modificadores if ci.pedido_item_id in items else None
                    ),
                )
                for ci in c.items
            ],
        )
        for c in comandas
    ]


@router.get("", response_model=KdsLeer)
async def cola_kds(
    estado: list[str] | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> KdsLeer:
    repo = SqlPedidosRepository(session)
    service = KdsService(repo)
    zonas = await service.listar_zonas()
    comandas = await service.listar(estados=estado)
    return KdsLeer(
        zonas=[{"id": z.id, "nombre": z.nombre} for z in zonas],
        comandas=await _componer(comandas, repo, zonas),
    )


@router.put("/comandas/{comanda_id}/estado", response_model=ComandaLeer)
async def avanzar_comanda(
    comanda_id: int,
    payload: CambioEstadoComanda,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> ComandaLeer:
    repo = SqlPedidosRepository(session)
    service = KdsService(repo)
    try:
        comanda = await service.cambiar_estado(comanda_id, payload.estado)
    except ComandaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comanda no encontrada") from exc
    except TransicionComandaInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Transición inválida: {exc}") from exc
    zonas = await service.listar_zonas()
    return (await _componer([comanda], repo, zonas))[0]


@router.get("/zonas")
async def listar_zonas(
    service: KdsService = Depends(get_kds_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[dict]:
    return [{"id": z.id, "nombre": z.nombre} for z in await service.listar_zonas()]


@router.post("/zonas", status_code=status.HTTP_201_CREATED)
async def crear_zona(
    payload: ZonaComandaCrear,
    service: KdsService = Depends(get_kds_service),
    _user: Principal = Depends(require_role("admin")),
) -> dict:
    zona = await service.crear_zona(payload.nombre)
    return {"id": zona.id, "nombre": zona.nombre}


@router.put("/ruteo", status_code=status.HTTP_204_NO_CONTENT)
async def rutear_producto(
    payload: RuteoComanda,
    service: KdsService = Depends(get_kds_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    await service.rutear(payload.producto_id, payload.zona_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
