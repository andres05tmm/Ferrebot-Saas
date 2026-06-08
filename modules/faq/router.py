"""Router del pack FAQ / conocimiento (backend del dashboard). Gateado por la capacidad `pack_faq`.

Sin el flag `pack_faq`, las rutas responden 404 (como si no existieran). RBAC: leer el conocimiento es
de **staff** (vendedor+, lo usa quien atiende); crear/editar/borrar es de **admin** (nutre el negocio).
La lógica vive en `FaqService`; aquí solo se valida, se mapea a HTTP y se serializa — sin SQL.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.faq.errors import ConocimientoInexistente
from modules.faq.repository import SqlConocimientoRepository
from modules.faq.schemas import ConocimientoCrear, ConocimientoLeer
from modules.faq.service import FaqService

# Todo el router exige el flag pack_faq (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/faq", tags=["faq"],
    dependencies=[Depends(require_feature("pack_faq"))],
)


def get_faq_service(session: AsyncSession = Depends(get_tenant_db)) -> FaqService:
    """Arma el `FaqService` sobre la sesión del tenant (los tests lo overridean)."""
    return FaqService(SqlConocimientoRepository(session))


@router.get("/conocimiento", response_model=list[ConocimientoLeer])
async def listar_conocimiento(
    incluir_inactivas: bool = Query(default=False),
    service: FaqService = Depends(get_faq_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ConocimientoLeer]:
    return await service.listar(solo_activas=not incluir_inactivas)


@router.post("/conocimiento", response_model=ConocimientoLeer, status_code=status.HTTP_201_CREATED)
async def crear_conocimiento(
    payload: ConocimientoCrear,
    service: FaqService = Depends(get_faq_service),
    _user: Principal = Depends(require_role("admin")),
) -> ConocimientoLeer:
    return await service.crear(payload)


@router.get("/conocimiento/{conocimiento_id}", response_model=ConocimientoLeer)
async def obtener_conocimiento(
    conocimiento_id: int,
    service: FaqService = Depends(get_faq_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ConocimientoLeer:
    try:
        return await service.obtener(conocimiento_id)
    except ConocimientoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.put("/conocimiento/{conocimiento_id}", response_model=ConocimientoLeer)
async def actualizar_conocimiento(
    conocimiento_id: int,
    payload: ConocimientoCrear,
    service: FaqService = Depends(get_faq_service),
    _user: Principal = Depends(require_role("admin")),
) -> ConocimientoLeer:
    try:
        return await service.actualizar(conocimiento_id, payload)
    except ConocimientoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/conocimiento/{conocimiento_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_conocimiento(
    conocimiento_id: int,
    service: FaqService = Depends(get_faq_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    try:
        await service.eliminar(conocimiento_id)
    except ConocimientoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
