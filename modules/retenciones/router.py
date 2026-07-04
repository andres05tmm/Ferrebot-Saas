"""Router de retenciones/INC (ADR 0027). Gateado por la feature `retenciones` (404 sin ella) y admin-only.

La lógica vive en `RetencionesService`; aquí solo se valida y se mapea a HTTP. Aplicar el motor a un
documento NO cambia su total: devuelve el resumen con `neto_a_recibir` (total − retenido).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.retenciones.repository import SqlRetencionesRepository
from modules.retenciones.schemas import ReglaLeer, ReglaUpsert, ResumenRetenciones
from modules.retenciones.service import RetencionesService, TipoRetencionInvalido

router = APIRouter(
    tags=["retenciones"], dependencies=[Depends(require_feature("retenciones"))]
)


def _service(session: AsyncSession) -> RetencionesService:
    return RetencionesService(SqlRetencionesRepository(session))


@router.get("/retenciones/config", response_model=list[ReglaLeer])
async def listar_config(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[ReglaLeer]:
    """Catálogo tributario del tenant (retefuente/ica/reteiva/inc/uvt). Vacío = opt-in (nada se retiene)."""
    return await _service(session).listar_config()


@router.put("/retenciones/config", response_model=ReglaLeer)
async def upsert_config(
    payload: ReglaUpsert,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> ReglaLeer:
    """Alta/edición de una regla por (tipo, concepto). Editable por la empresa; sin tarifas del código."""
    try:
        return await _service(session).upsert_regla(payload)
    except TipoRetencionInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"tipo inválido: {exc}") from exc


@router.post("/retenciones/venta/{venta_id}/aplicar", response_model=ResumenRetenciones)
async def aplicar_venta(
    venta_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> ResumenRetenciones:
    """Calcula y persiste las retenciones de una venta. El total de la venta queda INTACTO (404 si no existe)."""
    resumen = await _service(session).aplicar_a_venta(venta_id)
    if resumen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "venta no encontrada")
    return resumen


@router.post("/retenciones/compra/{compra_id}/aplicar", response_model=ResumenRetenciones)
async def aplicar_compra(
    compra_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> ResumenRetenciones:
    """Calcula y persiste las retenciones de una compra (agente retenedor). 404 si la compra no existe."""
    resumen = await _service(session).aplicar_a_compra(compra_id)
    if resumen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "compra no encontrada")
    return resumen


@router.get("/retenciones/{doc_tipo}/{doc_id}", response_model=ResumenRetenciones)
async def obtener_documento(
    doc_tipo: str,
    doc_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> ResumenRetenciones:
    """Renglones tributarios ya persistidos de un documento (`venta`/`compra`)."""
    if doc_tipo not in ("venta", "compra"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "doc_tipo inválido")
    return await _service(session).obtener_documento(doc_tipo=doc_tipo, doc_id=doc_id)
