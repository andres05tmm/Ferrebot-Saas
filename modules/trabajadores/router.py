"""Router de trabajadores (vertical construcción, contrato CRUD de la Fase 1).

Gateado por la capacidad `nomina` (feature-flags.md): sin ella el router entero responde 404 (como si no
existiera). RBAC = admin: la ficha del trabajador trae salario y datos de seguridad social. La lógica
vive en `TrabajadoresService`; aquí solo se valida, se mapea a HTTP y se serializa. El servicio se
inyecta por dependencia (los tests lo overridean con un fake, sin red ni Postgres).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.trabajadores.errors import TrabajadorDuplicado, TrabajadorInexistente
from modules.trabajadores.repository import SqlTrabajadoresRepository
from modules.trabajadores.schemas import (
    TipoVinculacion,
    TrabajadorActualizar,
    TrabajadorCrear,
    TrabajadorLeer,
)
from modules.trabajadores.service import TrabajadoresService

router = APIRouter(tags=["trabajadores"], dependencies=[Depends(require_feature("nomina"))])


def get_trabajadores_service(
    session: AsyncSession = Depends(get_tenant_db),
) -> TrabajadoresService:
    """Arma el `TrabajadoresService` sobre la sesión del tenant (los tests lo overridean con un fake)."""
    return TrabajadoresService(SqlTrabajadoresRepository(session))


@router.get("/trabajadores", response_model=list[TrabajadorLeer])
async def listar_trabajadores(
    tipo_vinculacion: TipoVinculacion | None = Query(
        default=None, description="Filtra por vínculo (DIRECTO/PATACALIENTE)"
    ),
    activo: bool | None = Query(default=None, description="Filtra por estado laboral"),
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[TrabajadorLeer]:
    """Trabajadores vigentes (excluye los dados de baja), filtrables por vínculo y por `activo`."""
    trabajadores = await service.listar(tipo_vinculacion=tipo_vinculacion, activo=activo)
    return [TrabajadorLeer.model_validate(t) for t in trabajadores]


@router.post("/trabajadores", response_model=TrabajadorLeer, status_code=status.HTTP_201_CREATED)
async def crear_trabajador(
    payload: TrabajadorCrear,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> TrabajadorLeer:
    """Da de alta un trabajador. Documento duplicado → 409."""
    try:
        trabajador = await service.crear(payload)
    except TrabajadorDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return TrabajadorLeer.model_validate(trabajador)


@router.get("/trabajadores/{trabajador_id}", response_model=TrabajadorLeer)
async def obtener_trabajador(
    trabajador_id: int,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> TrabajadorLeer:
    try:
        trabajador = await service.obtener(trabajador_id)
    except TrabajadorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return TrabajadorLeer.model_validate(trabajador)


@router.patch("/trabajadores/{trabajador_id}", response_model=TrabajadorLeer)
async def actualizar_trabajador(
    trabajador_id: int,
    payload: TrabajadorActualizar,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> TrabajadorLeer:
    """Parche parcial (solo los campos enviados). 404 si no existe; 409 si el documento choca."""
    try:
        trabajador = await service.actualizar(trabajador_id, payload)
    except TrabajadorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TrabajadorDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return TrabajadorLeer.model_validate(trabajador)


@router.delete("/trabajadores/{trabajador_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_trabajador(
    trabajador_id: int,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    """Baja lógica (soft delete). 404 si no existe o ya estaba dado de baja; 204 si se marcó."""
    try:
        await service.eliminar(trabajador_id)
    except TrabajadorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
