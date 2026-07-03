"""Router del frente de pagos (dashboard). Gateado por `pagos_online`.

Sin el flag, 404. RBAC: staff LEE los cobros (necesita ver qué está pendiente al despachar);
cerrarlos a mano (pagado manual / cancelar) es de admin (mueve la verdad del dinero).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.schemas import CobroLeer
from modules.pagos.service import CobroInexistente, PagosService, TransicionInvalida

router = APIRouter(
    prefix="/pagos", tags=["pagos"],
    dependencies=[Depends(require_feature("pagos_online"))],
)


def get_pagos_service(session: AsyncSession = Depends(get_tenant_db)) -> PagosService:
    """`PagosService` SIN PSP: el dashboard lista y cierra a mano; el PSP vive en el worker/agente."""
    return PagosService(SqlPagosRepository(session))


@router.get("/cobros", response_model=list[CobroLeer])
async def listar_cobros(
    estado: list[str] | None = Query(default=None),
    service: PagosService = Depends(get_pagos_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[CobroLeer]:
    return await service.listar(estados=estado)


@router.post("/cobros/{cobro_id}/pagado-manual", response_model=CobroLeer)
async def marcar_pagado_manual(
    cobro_id: int,
    service: PagosService = Depends(get_pagos_service),
    _user: Principal = Depends(require_role("admin")),
) -> CobroLeer:
    try:
        return await service.marcar_pagado_manual(cobro_id)
    except CobroInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cobro no encontrado") from exc
    except TransicionInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/cobros/{cobro_id}/cancelar", response_model=CobroLeer)
async def cancelar_cobro(
    cobro_id: int,
    service: PagosService = Depends(get_pagos_service),
    _user: Principal = Depends(require_role("admin")),
) -> CobroLeer:
    try:
        return await service.cancelar(cobro_id)
    except CobroInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cobro no encontrado") from exc
    except TransicionInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
