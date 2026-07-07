"""Router de obras y reportes diarios (vertical construcción, contrato CRUD de la Fase 1).

Gateado por la capacidad `obras` (feature-flags.md): sin ella el router entero responde 404. RBAC: las
LECTURAS son de rol `vendedor` (personal de campo consulta y reporta avance); las MUTACIONES de la obra
(crear/editar/transición/baja) son de `admin`. Las transiciones de estado van por su endpoint dedicado
`PATCH /obras/{id}/estado`, que valida el ciclo de vida en el servicio (nada de estados imposibles).

La lógica vive en `ObrasService`; aquí solo se valida, se mapea a HTTP y se serializa. El servicio se
inyecta por dependencia (los tests lo overridean con un fake, sin red ni Postgres).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.obra.errors import ObraInexistente, TransicionEstadoInvalida
from modules.obra.repository import SqlObrasRepository
from modules.obra.schemas import (
    EstadoObra,
    ObraActualizar,
    ObraCrear,
    ObraEstadoCambiar,
    ObraLeer,
    ObraResumen,
    ReporteDiarioCrear,
    ReporteDiarioLeer,
)
from modules.obra.service import ObrasService

router = APIRouter(tags=["obras"], dependencies=[Depends(require_feature("obras"))])


def get_obras_service(session: AsyncSession = Depends(get_tenant_db)) -> ObrasService:
    """Arma el `ObrasService` sobre la sesión del tenant (los tests lo overridean con un fake)."""
    return ObrasService(SqlObrasRepository(session))


@router.get("/obras", response_model=list[ObraLeer])
async def listar_obras(
    cliente_id: int | None = Query(default=None, description="Filtra por cliente"),
    estado: EstadoObra | None = Query(default=None, description="Filtra por estado"),
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ObraLeer]:
    """Obras vigentes (excluye las dadas de baja), filtrables por cliente y estado."""
    obras = await service.listar(cliente_id=cliente_id, estado=estado)
    return [ObraLeer.model_validate(o) for o in obras]


@router.post("/obras", response_model=ObraLeer, status_code=status.HTTP_201_CREATED)
async def crear_obra(
    payload: ObraCrear,
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("admin")),
) -> ObraLeer:
    """Da de alta una obra suelta (arranca PLANIFICADA; la conversión desde cotización es Fase 2)."""
    obra = await service.crear(payload)
    return ObraLeer.model_validate(obra)


@router.get("/obras/{obra_id}", response_model=ObraResumen)
async def obtener_obra(
    obra_id: int,
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ObraResumen:
    """Detalle de la obra + conteos baratos de su operación (máquinas/trabajadores/reportes). 404 si no existe."""
    try:
        obra, conteos = await service.resumen(obra_id)
    except ObraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ObraResumen(
        **ObraLeer.model_validate(obra).model_dump(),
        maquinas_asignadas=conteos.maquinas_asignadas,
        trabajadores_asignados=conteos.trabajadores_asignados,
        reportes_diarios=conteos.reportes_diarios,
    )


@router.patch("/obras/{obra_id}", response_model=ObraLeer)
async def actualizar_obra(
    obra_id: int,
    payload: ObraActualizar,
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("admin")),
) -> ObraLeer:
    """Parche parcial de metadatos (no cambia `estado`). 404 si no existe."""
    try:
        obra = await service.actualizar(obra_id, payload)
    except ObraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ObraLeer.model_validate(obra)


@router.patch("/obras/{obra_id}/estado", response_model=ObraLeer)
async def cambiar_estado_obra(
    obra_id: int,
    payload: ObraEstadoCambiar,
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("admin")),
) -> ObraLeer:
    """Aplica una transición de estado válida. 404 si no existe; 409 si la transición no se permite."""
    try:
        obra = await service.cambiar_estado(obra_id, payload.estado)
    except ObraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TransicionEstadoInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ObraLeer.model_validate(obra)


@router.delete("/obras/{obra_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_obra(
    obra_id: int,
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    """Baja lógica (soft delete). 404 si no existe o ya estaba dada de baja; 204 si se marcó."""
    try:
        await service.eliminar(obra_id)
    except ObraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/obras/{obra_id}/reportes-diarios",
    response_model=ReporteDiarioLeer,
    status_code=status.HTTP_201_CREATED,
)
async def crear_reporte_diario(
    obra_id: int,
    payload: ReporteDiarioCrear,
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ReporteDiarioLeer:
    """Registra un reporte diario de avance de la obra. 404 si la obra no existe."""
    try:
        reporte = await service.crear_reporte(obra_id, payload)
    except ObraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ReporteDiarioLeer.model_validate(reporte)


@router.get(
    "/obras/{obra_id}/reportes-diarios", response_model=list[ReporteDiarioLeer]
)
async def listar_reportes_diarios(
    obra_id: int,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ObrasService = Depends(get_obras_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ReporteDiarioLeer]:
    """Reportes diarios de la obra (más recientes primero). 404 si la obra no existe."""
    try:
        reportes = await service.listar_reportes(obra_id, limite=limite, offset=offset)
    except ObraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [ReporteDiarioLeer.model_validate(r) for r in reportes]
