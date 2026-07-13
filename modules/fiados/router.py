"""Router de fiados (`/fiados/*`). RBAC: vendedor (api-contract.md). Núcleo: sin require_feature."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.db.session import get_tenant_db
from modules.fiados.errors import ClienteInexistente, FiadoInexistente, SobreAbono
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.schemas import (
    AbonoCrear,
    DeudaLeer,
    FiadoCrear,
    FiadoLeer,
    MovimientoFiadoLeer,
)
from modules.fiados.service import FiadosService

router = APIRouter(tags=["fiados"])


def _service(session: AsyncSession) -> FiadosService:
    return FiadosService(SqlFiadosRepository(session))


@router.post("/fiados", response_model=FiadoLeer, status_code=status.HTTP_201_CREATED)
async def crear_fiado(
    payload: FiadoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> FiadoLeer:
    try:
        res = await _service(session).crear(
            cliente_id=payload.cliente_id, venta_id=payload.venta_id, monto=payload.monto,
            idempotency_key=idempotency_key,
        )
    except ClienteInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return FiadoLeer.model_validate(res.fiado)


@router.post("/fiados/{fiado_id}/abono", response_model=MovimientoFiadoLeer, status_code=status.HTTP_201_CREATED)
async def abonar_fiado(
    fiado_id: int,
    payload: AbonoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MovimientoFiadoLeer:
    try:
        res = await _service(session).abonar(
            fiado_id=fiado_id, monto=payload.monto, idempotency_key=idempotency_key,
        )
    except FiadoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SobreAbono as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return MovimientoFiadoLeer.model_validate(res.movimiento)


@router.get("/fiados", response_model=list[FiadoLeer])
async def listar_fiados(
    cliente_id: int = Query(..., description="Cliente cuyos fiados con saldo se listan"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[FiadoLeer]:
    """Fiados VIVOS (saldo > 0) de un cliente, viejos primero. Alimenta el modal de abono del
    dashboard (F2.3). Cliente sin fiados → lista vacía."""
    fiados = await _service(session).fiados_de(cliente_id)
    return [FiadoLeer.model_validate(f) for f in fiados]


@router.get("/fiados/deudas", response_model=list[DeudaLeer])
async def listar_deudas(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[DeudaLeer]:
    deudas = await _service(session).deudas()
    return [DeudaLeer(**fila) for fila in deudas]
