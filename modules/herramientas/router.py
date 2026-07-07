"""Router de herramientas (vertical construcción). Gate de capacidad `herramientas` (feature-flags.md):
sin ella, todo el router responde 404. Lecturas: rol `vendedor`; mutaciones: `admin`. La lógica vive en
`HerramientasService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.herramientas.errors import (
    CodigoHerramientaDuplicado,
    HerramientaInexistente,
)
from modules.herramientas.repository import SqlHerramientasRepository
from modules.herramientas.schemas import (
    EstadoHerramienta,
    HerramientaActualizar,
    HerramientaCrear,
    HerramientaLeer,
)
from modules.herramientas.service import HerramientasService

router = APIRouter(tags=["herramientas"], dependencies=[Depends(require_feature("herramientas"))])


def _service(session: AsyncSession) -> HerramientasService:
    return HerramientasService(SqlHerramientasRepository(session))


@router.get("/herramientas", response_model=list[HerramientaLeer])
async def listar_herramientas(
    estado: EstadoHerramienta | None = Query(default=None, description="Filtra por estado"),
    q: str | None = Query(default=None, description="Filtra por código o nombre (ILIKE)"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[HerramientaLeer]:
    """Herramientas vivas (no eliminadas), filtrables por estado y por texto."""
    herramientas = await _service(session).listar(estado=estado, q=q)
    return [HerramientaLeer.model_validate(h) for h in herramientas]


@router.post("/herramientas", response_model=HerramientaLeer, status_code=status.HTTP_201_CREATED)
async def crear_herramienta(
    payload: HerramientaCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> HerramientaLeer:
    """Da de alta una herramienta. Código duplicado → 409."""
    try:
        herramienta = await _service(session).crear(payload)
    except CodigoHerramientaDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return HerramientaLeer.model_validate(herramienta)


@router.get("/herramientas/{herramienta_id}", response_model=HerramientaLeer)
async def obtener_herramienta(
    herramienta_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> HerramientaLeer:
    try:
        herramienta = await _service(session).obtener(herramienta_id)
    except HerramientaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return HerramientaLeer.model_validate(herramienta)


@router.patch("/herramientas/{herramienta_id}", response_model=HerramientaLeer)
async def actualizar_herramienta(
    herramienta_id: int,
    payload: HerramientaActualizar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> HerramientaLeer:
    """Edición parcial (solo los campos enviados). 404 si no existe; 409 si el código lo usa otra."""
    try:
        herramienta = await _service(session).actualizar(herramienta_id, payload)
    except HerramientaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CodigoHerramientaDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return HerramientaLeer.model_validate(herramienta)


@router.delete("/herramientas/{herramienta_id}")
async def eliminar_herramienta(
    herramienta_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, object]:
    """Soft delete: la herramienta queda con `eliminado_en` (no se borra)."""
    try:
        await _service(session).eliminar(herramienta_id)
    except HerramientaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"herramienta_id": herramienta_id, "eliminado": True}
