"""Router de conciliación bancaria (`/bancos/*`). Gateado por `conciliacion_bancaria`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: TODO el router es de **admin** —
los movimientos bancarios y su cruce con ventas/gastos/CxP son información sensible del negocio. La
lógica vive en `BancosService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config.timezone import now_co
from core.db.session import get_tenant_db
from modules.bancos.errors import ConciliacionInvalida, MovimientoBancarioInexistente
from modules.bancos.repository import SqlBancosRepository
from modules.bancos.schemas import (
    ConciliarConfirmar,
    IngestaResultado,
    MovimientoBancarioIngesta,
    MovimientoBancarioLeer,
    MovimientoConCandidatos,
)
from modules.bancos.service import BancosService

router = APIRouter(
    prefix="/bancos", tags=["bancos"],
    dependencies=[Depends(require_feature("conciliacion_bancaria"))],
)


def get_bancos_service(session: AsyncSession = Depends(get_tenant_db)) -> BancosService:
    return BancosService(SqlBancosRepository(session))


@router.post("/ingesta", response_model=IngestaResultado)
async def ingerir_extracto(
    movimientos: list[MovimientoBancarioIngesta],
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> IngestaResultado:
    """Ingiere líneas de un extracto. Idempotente por `referencia_bancaria` (reprocesar no duplica)."""
    return await service.ingestar(movimientos)


@router.post("/sugerir")
async def sugerir(
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, int]:
    """Corre el match semi-automático: marca `sugerido` los de candidato único (ambiguos jamás)."""
    return {"sugeridos": await service.sugerir_pendientes()}


@router.get("/movimientos", response_model=list[MovimientoConCandidatos])
async def listar_movimientos(
    estado: str | None = Query(default=None),
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[MovimientoConCandidatos]:
    """Movimientos del extracto (filtrables por estado) con sus candidatos internos vigentes."""
    return await service.listar(estado=estado)


@router.post("/movimientos/{mov_id}/conciliar", response_model=MovimientoBancarioLeer)
async def conciliar(
    mov_id: int,
    payload: ConciliarConfirmar,
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> MovimientoBancarioLeer:
    """Confirma EXPLÍCITAMENTE el enlace elegido (→ conciliado). Solo enlaza; no toca saldos."""
    try:
        return await service.confirmar(
            mov_id, tipo=payload.tipo, id_interno=payload.id_interno, ahora=now_co()
        )
    except MovimientoBancarioInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ConciliacionInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
