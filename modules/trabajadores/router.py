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
from modules.trabajadores.errors import (
    AsignacionInexistente,
    AsignacionSolapada,
    ObraNoAsignable,
    RangoAsignacionInvalido,
    TrabajadorDuplicado,
    TrabajadorInexistente,
)
from modules.trabajadores.repository import SqlTrabajadoresRepository
from modules.trabajadores.schemas import (
    AsignacionTrabajadorActualizar,
    AsignacionTrabajadorCrear,
    AsignacionTrabajadorLeer,
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


# --- Asignaciones trabajador→obra (Calendario de obra): lectura vendedor, mutaciones admin ----------
# Segmento estático `/asignaciones` tras `{trabajador_id}`: no colisiona con `/trabajadores/{id}`.


def _obra_no_asignable_http(exc: ObraNoAsignable) -> HTTPException:
    """Mapea `ObraNoAsignable` al código del contrato: obra inexistente → 404; LIQUIDADA → 409."""
    codigo = status.HTTP_404_NOT_FOUND if exc.motivo == "inexistente" else status.HTTP_409_CONFLICT
    return HTTPException(codigo, str(exc))


@router.get(
    "/trabajadores/{trabajador_id}/asignaciones",
    response_model=list[AsignacionTrabajadorLeer],
)
async def listar_asignaciones_trabajador(
    trabajador_id: int,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[AsignacionTrabajadorLeer]:
    """Asignaciones a obra del trabajador (lectura de personal de campo). 404 si el trabajador no existe."""
    try:
        asignaciones = await service.listar_asignaciones(trabajador_id)
    except TrabajadorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [AsignacionTrabajadorLeer.model_validate(a) for a in asignaciones]


@router.post(
    "/trabajadores/{trabajador_id}/asignaciones",
    response_model=AsignacionTrabajadorLeer,
    status_code=status.HTTP_201_CREATED,
)
async def crear_asignacion_trabajador(
    trabajador_id: int,
    payload: AsignacionTrabajadorCrear,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> AsignacionTrabajadorLeer:
    """Asigna el trabajador a una obra. `fecha_inicio` default hoy Colombia. 404 si trabajador/obra no
    existen; 409 si la obra está LIQUIDADA o el rango se solapa con otra asignación activa."""
    try:
        asig = await service.crear_asignacion(trabajador_id, payload)
    except TrabajadorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObraNoAsignable as exc:
        raise _obra_no_asignable_http(exc) from exc
    except AsignacionSolapada as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AsignacionTrabajadorLeer.model_validate(asig)


@router.patch(
    "/trabajadores/{trabajador_id}/asignaciones/{asignacion_id}",
    response_model=AsignacionTrabajadorLeer,
)
async def actualizar_asignacion_trabajador(
    trabajador_id: int,
    asignacion_id: int,
    payload: AsignacionTrabajadorActualizar,
    service: TrabajadoresService = Depends(get_trabajadores_service),
    _user: Principal = Depends(require_role("admin")),
) -> AsignacionTrabajadorLeer:
    """Edición parcial de una asignación (cerrar, mover fecha_fin). 404 si no existe para ese trabajador;
    409 si el nuevo rango se solapa con otra activa."""
    try:
        asig = await service.actualizar_asignacion(trabajador_id, asignacion_id, payload)
    except AsignacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AsignacionSolapada as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RangoAsignacionInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return AsignacionTrabajadorLeer.model_validate(asig)


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
