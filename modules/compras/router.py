"""Router de compras (núcleo: 'compras' no es capacidad opcional → sin require_feature).

Registrar/listar compras es solo de admin (RBAC). Lo fiscal (compras_fiscal/RADIAN) va gateado y es de
otro slice. La lógica vive en ComprasService; aquí solo se valida y se mapea a HTTP.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.db.session import get_tenant_db
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import CompraCrear, CompraLeer
from modules.compras.service import ComprasService

router = APIRouter(tags=["compras"])


def _service(session: AsyncSession) -> ComprasService:
    return ComprasService(SqlComprasRepository(session))


@router.post("/compras", response_model=CompraLeer, status_code=status.HTTP_201_CREATED)
async def crear_compra(
    payload: CompraCrear,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
) -> CompraLeer:
    """Registra una compra: suma stock (ENTRADA) y fija el costo de compra de cada producto."""
    return await _service(session).registrar(payload, usuario_id=user.user_id)


@router.get("/compras", response_model=list[CompraLeer])
async def listar_compras(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[CompraLeer]:
    """Historial de compras del rango (default mes en curso, hora Colombia)."""
    return await _service(session).listar(desde=desde, hasta=hasta)
