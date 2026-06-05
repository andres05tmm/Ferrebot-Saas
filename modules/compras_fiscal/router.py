"""Router de compras fiscales (gateado por la capacidad OPCIONAL `compras_fiscal`).

Sin la feature, las rutas responden 404 (como si no existieran, feature-flags.md). RBAC = admin. Este
slice es solo DATOS: NO toca RADIAN/DIAN (eventos 030-033, acuse) ni MATIAS. La lógica vive en
ComprasFiscalService; aquí solo se valida y se mapea a HTTP.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.compras_fiscal.errors import CompraInexistente
from modules.compras_fiscal.repository import SqlComprasFiscalRepository
from modules.compras_fiscal.schemas import CompraFiscalCrear, CompraFiscalLeer
from modules.compras_fiscal.service import ComprasFiscalService

router = APIRouter(tags=["compras-fiscal"], dependencies=[Depends(require_feature("compras_fiscal"))])


def _service(session: AsyncSession) -> ComprasFiscalService:
    return ComprasFiscalService(SqlComprasFiscalRepository(session))


@router.post("/compras-fiscal", response_model=CompraFiscalLeer, status_code=status.HTTP_201_CREATED)
async def crear_compra_fiscal(
    payload: CompraFiscalCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Registra una compra fiscal con su desglose de IVA (alimenta el Libro IVA)."""
    return await _service(session).registrar(payload)


@router.get("/compras-fiscal", response_model=list[CompraFiscalLeer])
async def listar_compras_fiscal(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[CompraFiscalLeer]:
    """Compras fiscales del rango (default mes en curso, hora Colombia)."""
    return await _service(session).listar(desde=desde, hasta=hasta)


@router.post("/compras/{compra_id}/to-fiscal", response_model=CompraFiscalLeer)
async def compra_a_fiscal(
    compra_id: int,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Crea una compra fiscal a partir de una compra normal (idempotente). 201 si la crea, 200 si ya
    existía; 404 si la compra no existe."""
    try:
        fiscal, creada = await _service(session).desde_compra(compra_id)
    except CompraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    response.status_code = status.HTTP_201_CREATED if creada else status.HTTP_200_OK
    return fiscal
