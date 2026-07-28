"""Router de conciliación bancaria (`/bancos/*`). Gateado por `conciliacion_bancaria`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: TODO el router es de **admin** —
los movimientos bancarios y su cruce con ventas/gastos/CxP son información sensible del negocio. La
lógica vive en `BancosService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config.timezone import now_co
from core.db.session import get_tenant_db
from modules.bancos.config import get_alias_cuentas
from modules.bancos.errors import ConciliacionInvalida, MovimientoBancarioInexistente
from modules.bancos.repository import SqlBancosRepository
from modules.bancos.schemas import (
    ConciliarConfirmar,
    IngestaResultado,
    MovimientoBancarioIngesta,
    MovimientoBancarioLeer,
    MovimientoConCandidatos,
    RemitenteRecurrente,
    TotalesBancarios,
)
from modules.bancos.service import BancosService

router = APIRouter(
    prefix="/bancos", tags=["bancos"],
    dependencies=[Depends(require_feature("conciliacion_bancaria"))],
)


def get_bancos_service(session: AsyncSession = Depends(get_tenant_db)) -> BancosService:
    return BancosService(SqlBancosRepository(session))


@router.post("/ingesta", response_model=IngestaResultado)
async def ingerir_extracto(
    movimientos: list[MovimientoBancarioIngesta],
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> IngestaResultado:
    """Ingiere líneas de un extracto. Idempotente por `referencia_bancaria` (reprocesar no duplica)."""
    return await service.ingestar(movimientos)


@router.post("/sugerir")
async def sugerir(
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, int]:
    """Corre el match semi-automático: marca `sugerido` los de candidato único (ambiguos jamás)."""
    return {"sugeridos": await service.sugerir_pendientes()}


@router.get("/totales", response_model=TotalesBancarios)
async def totales(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    service: BancosService = Depends(get_bancos_service),
    alias: dict[str, str] = Depends(get_alias_cuentas),
    _user: Principal = Depends(require_role("admin")),
) -> TotalesBancarios:
    """Cuánta plata entró (solo créditos) en el período, en total y por cuenta. Default: mes en curso."""
    return await service.totales(desde=desde, hasta=hasta, alias=alias)


@router.get("/remitentes", response_model=list[RemitenteRecurrente])
async def remitentes(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    min_veces: int = Query(default=2, ge=1, le=100),
    limite: int = Query(default=20, ge=1, le=100),
    cuenta: str | None = Query(default=None, max_length=20),
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[RemitenteRecurrente]:
    """Quién mandó plata más de una vez en el período. Reporte de lectura: no toca `clientes`."""
    return await service.remitentes(
        desde=desde, hasta=hasta, min_veces=min_veces, limite=limite, cuenta=cuenta
    )


@router.get("/movimientos", response_model=list[MovimientoConCandidatos])
async def listar_movimientos(
    estado: str | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    incluir_descartados: bool = Query(default=False),
    cuenta: str | None = Query(default=None, max_length=20),
    limite: int = Query(default=200, ge=1, le=500),
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[MovimientoConCandidatos]:
    """Movimientos bancarios (filtrables por estado/período/cuenta) con sus candidatos vigentes.

    `cuenta` es la lente del tab: sin ella, todas; con el centinela `sin_cuenta`, las que el parser
    no pudo leer; con un número, esa cuenta.
    """
    return await service.listar(
        estado=estado, desde=desde, hasta=hasta,
        incluir_descartados=incluir_descartados, cuenta=cuenta, limite=limite,
    )


@router.post("/movimientos/{mov_id}/conciliar", response_model=MovimientoBancarioLeer)
async def conciliar(
    mov_id: int,
    payload: ConciliarConfirmar,
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> MovimientoBancarioLeer:
    """Confirma EXPLÍCITAMENTE el enlace elegido (→ conciliado). Solo enlaza; no toca saldos."""
    try:
        return await service.confirmar(
            mov_id, tipo=payload.tipo, id_interno=payload.id_interno, ahora=now_co()
        )
    except MovimientoBancarioInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ConciliacionInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/movimientos/{mov_id}/descarte", response_model=MovimientoBancarioLeer)
async def descartar(
    mov_id: int,
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> MovimientoBancarioLeer:
    """Marca el movimiento como "no es venta" (plata personal o de la casa). Idempotente."""
    return await _descarte(service, mov_id, descartar=True)


@router.delete("/movimientos/{mov_id}/descarte", response_model=MovimientoBancarioLeer)
async def deshacer_descarte(
    mov_id: int,
    service: BancosService = Depends(get_bancos_service),
    _user: Principal = Depends(require_role("admin")),
) -> MovimientoBancarioLeer:
    """Deshace el "no es venta": el movimiento vuelve al pendiente. Idempotente."""
    return await _descarte(service, mov_id, descartar=False)


async def _descarte(
    service: BancosService, mov_id: int, *, descartar: bool
) -> MovimientoBancarioLeer:
    try:
        return await service.descartar(mov_id, descartar=descartar, ahora=now_co())
    except MovimientoBancarioInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ConciliacionInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
