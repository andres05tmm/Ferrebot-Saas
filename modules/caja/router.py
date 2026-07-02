"""Routers de caja (`/caja/*`) y gastos (`/gastos`). RBAC: vendedor (api-contract.md).

Feature fina `caja` (ADR 0021, antes pack `pos`): sin la capacidad, ambos routers responden 404.
La lógica vive en CajaService; aquí se valida, se resuelve permiso y se mapea a HTTP.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.schemas import (
    AperturaCrear,
    CajaLeer,
    CierreCrear,
    GastoCrear,
    GastoLeer,
    MovimientoCrear,
    MovimientoLeer,
)
from modules.caja.service import CajaService

router = APIRouter(tags=["caja"], dependencies=[Depends(require_feature("caja"))])
gastos_router = APIRouter(tags=["gastos"], dependencies=[Depends(require_feature("caja"))])


def _service(session: AsyncSession) -> CajaService:
    return CajaService(SqlCajaRepository(session))


@router.get("/caja/actual", response_model=CajaLeer)
async def caja_actual(
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> CajaLeer:
    caja = await _service(session).actual(user.user_id)
    if caja is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay caja abierta")
    return CajaLeer.model_validate(caja)


@router.post("/caja/apertura", response_model=CajaLeer, status_code=status.HTTP_201_CREATED)
async def abrir_caja(
    payload: AperturaCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> CajaLeer:
    res = await _service(session).abrir(usuario_id=user.user_id, saldo_inicial=payload.saldo_inicial)
    if res.replay:
        response.status_code = status.HTTP_200_OK   # ya tenía caja abierta
    return CajaLeer.model_validate(res.caja)


@router.post("/caja/cierre", response_model=CajaLeer)
async def cerrar_caja(
    payload: CierreCrear,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> CajaLeer:
    try:
        caja = await _service(session).cerrar(usuario_id=user.user_id, saldo_contado=payload.saldo_contado)
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return CajaLeer.model_validate(caja)


@router.post("/caja/movimiento", response_model=MovimientoLeer, status_code=status.HTTP_201_CREATED)
async def registrar_movimiento(
    payload: MovimientoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MovimientoLeer:
    try:
        res = await _service(session).registrar_movimiento(
            usuario_id=user.user_id, tipo=payload.tipo, monto=payload.monto,
            concepto=payload.concepto, idempotency_key=idempotency_key,
        )
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return MovimientoLeer.model_validate(res.movimiento)


@gastos_router.post("/gastos", response_model=GastoLeer, status_code=status.HTTP_201_CREATED)
async def registrar_gasto(
    payload: GastoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> GastoLeer:
    try:
        res = await _service(session).registrar_gasto(
            usuario_id=user.user_id, categoria=payload.categoria, monto=payload.monto,
            concepto=payload.concepto, idempotency_key=idempotency_key,
        )
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return GastoLeer.model_validate(res.gasto)


@gastos_router.get("/gastos", response_model=list[GastoLeer])
async def listar_gastos(
    desde: datetime | None = None,
    hasta: datetime | None = None,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[GastoLeer]:
    gastos = await SqlCajaRepository(session).listar_gastos(
        desde=desde, hasta=hasta, limite=limite, offset=offset
    )
    return [GastoLeer.model_validate(g) for g in gastos]
