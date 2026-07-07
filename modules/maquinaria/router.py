"""Router de maquinaria (vertical construcción). Gate de capacidad `maquinaria` (feature-flags.md): sin
ella, todo el router responde 404 (como si no existiera). Lecturas: rol `vendedor`; mutaciones: `admin`
(calca la partición de `modules/inventario/router.py`). La lógica vive en `MaquinariaService`; aquí solo
se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.maquinaria.errors import CodigoMaquinaDuplicado, MaquinaInexistente
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import (
    AsignacionMaquinaObraLeer,
    EstadoMaquina,
    MaquinaActualizar,
    MaquinaCrear,
    MaquinaLeer,
    RegistroHorasMaquinaLeer,
)
from modules.maquinaria.service import MaquinariaService

router = APIRouter(tags=["maquinaria"], dependencies=[Depends(require_feature("maquinaria"))])


def _service(session: AsyncSession) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(session))


@router.get("/maquinas", response_model=list[MaquinaLeer])
async def listar_maquinas(
    estado: EstadoMaquina | None = Query(default=None, description="Filtra por estado de la máquina"),
    q: str | None = Query(default=None, description="Filtra por código o nombre (ILIKE)"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[MaquinaLeer]:
    """Máquinas vivas (no eliminadas), filtrables por estado y por texto."""
    maquinas = await _service(session).listar(estado=estado, q=q)
    return [MaquinaLeer.model_validate(m) for m in maquinas]


@router.post("/maquinas", response_model=MaquinaLeer, status_code=status.HTTP_201_CREATED)
async def crear_maquina(
    payload: MaquinaCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> MaquinaLeer:
    """Da de alta una máquina. Código duplicado → 409."""
    try:
        maquina = await _service(session).crear(payload)
    except CodigoMaquinaDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return MaquinaLeer.model_validate(maquina)


@router.get("/maquinas/{maquina_id}", response_model=MaquinaLeer)
async def obtener_maquina(
    maquina_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> MaquinaLeer:
    try:
        maquina = await _service(session).obtener(maquina_id)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return MaquinaLeer.model_validate(maquina)


@router.patch("/maquinas/{maquina_id}", response_model=MaquinaLeer)
async def actualizar_maquina(
    maquina_id: int,
    payload: MaquinaActualizar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> MaquinaLeer:
    """Edición parcial (solo los campos enviados). 404 si no existe; 409 si el código lo usa otra."""
    try:
        maquina = await _service(session).actualizar(maquina_id, payload)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CodigoMaquinaDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return MaquinaLeer.model_validate(maquina)


@router.delete("/maquinas/{maquina_id}")
async def eliminar_maquina(
    maquina_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, object]:
    """Soft delete: la máquina queda con `eliminado_en` (no se borra; la referencian horas/asignaciones)."""
    try:
        await _service(session).eliminar(maquina_id)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"maquina_id": maquina_id, "eliminado": True}


@router.get("/maquinas/{maquina_id}/asignaciones", response_model=list[AsignacionMaquinaObraLeer])
async def listar_asignaciones(
    maquina_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[AsignacionMaquinaObraLeer]:
    """Asignaciones a obra de la máquina (solo lectura; el alta es de Fase 3)."""
    asignaciones = await _service(session).listar_asignaciones(maquina_id)
    return [AsignacionMaquinaObraLeer.model_validate(a) for a in asignaciones]


@router.get("/maquinas/{maquina_id}/horas", response_model=list[RegistroHorasMaquinaLeer])
async def listar_horas(
    maquina_id: int,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[RegistroHorasMaquinaLeer]:
    """Partes de horas de la máquina (solo lectura; el registro es de Fase 3)."""
    horas = await _service(session).listar_horas(maquina_id, limite=limite, offset=offset)
    return [RegistroHorasMaquinaLeer.model_validate(h) for h in horas]
