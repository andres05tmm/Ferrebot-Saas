"""Router del pack cobranza (backend de la página Cartera del dashboard). Gateado por `pack_cobranza`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: TODO el router es de **admin** —
la cartera es información sensible del negocio (Habeas Data: nombre + teléfono + saldo del cliente
final). La lógica vive en `CobranzaService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.cobranza.errors import PagoReportadoInexistente
from modules.cobranza.repository import SqlCobranzaRepository
from modules.cobranza.schemas import (
    CobranzaConfigActualizar,
    CobranzaConfigLeer,
    DeudorLeer,
    OptOutActualizar,
    PagoReportadoLeer,
    PromesaLeer,
)
from modules.cobranza.service import CobranzaService

# Todo el router exige el flag pack_cobranza (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/cobranza", tags=["cobranza"],
    dependencies=[Depends(require_feature("pack_cobranza"))],
)


def get_cobranza_service(session: AsyncSession = Depends(get_tenant_db)) -> CobranzaService:
    """Arma el `CobranzaService` sobre la sesión del tenant (los tests lo overridean)."""
    return CobranzaService(SqlCobranzaRepository(session))


@router.get("/deudores", response_model=list[DeudorLeer])
async def listar_deudores(
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[DeudorLeer]:
    return await service.listar_deudores()


@router.get("/config", response_model=CobranzaConfigLeer)
async def obtener_config(
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> CobranzaConfigLeer:
    return await service.obtener_config()


@router.put("/config", response_model=CobranzaConfigLeer)
async def actualizar_config(
    payload: CobranzaConfigActualizar,
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> CobranzaConfigLeer:
    return await service.guardar_config(payload)


@router.get("/promesas", response_model=list[PromesaLeer])
async def listar_promesas(
    estado: str | None = Query(default=None),
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[PromesaLeer]:
    return await service.listar_promesas(estado=estado)


@router.get("/pagos-reportados", response_model=list[PagoReportadoLeer])
async def listar_pagos_reportados(
    incluir_verificados: bool = Query(default=False),
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[PagoReportadoLeer]:
    return await service.listar_pagos_reportados(solo_pendientes=not incluir_verificados)


@router.post("/pagos-reportados/{pago_id}/verificar", response_model=PagoReportadoLeer)
async def verificar_pago_reportado(
    pago_id: int,
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> PagoReportadoLeer:
    try:
        return await service.verificar_pago_reportado(pago_id)
    except PagoReportadoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pago reportado no encontrado") from exc


@router.put("/clientes/{cliente_id}/opt-out", status_code=status.HTTP_204_NO_CONTENT)
async def fijar_opt_out(
    cliente_id: int,
    payload: OptOutActualizar,
    service: CobranzaService = Depends(get_cobranza_service),
    _user: Principal = Depends(require_role("admin")),
) -> None:
    await service.fijar_opt_out(cliente_id, payload.opt_out)
