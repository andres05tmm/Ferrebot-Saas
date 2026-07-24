"""Router de la cola de impresión (ADR 0033 D3). Gateado por `impresion` (404 sin él).

RBAC: toda la superficie es de **staff** (vendedor+): el agente local opera con la identidad del
dispositivo y el dashboard reimprime/pide precuentas. Sin SQL aquí.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.deps import get_current_user
from core.auth.features import require_feature
from core.auth.rbac import satisface
from core.db.session import control_session, get_control_db, get_tenant_db
from modules.impresion.dispositivos import (
    emitir_dispositivo,
    listar_dispositivos,
    revocar_dispositivo,
    validar_token,
)
from modules.impresion.repository import SqlImpresionRepository
from modules.impresion.schemas import AckTrabajo, CrearTrabajo, DispositivoCrear, TrabajoLeer
from modules.impresion.service import ImpresionService, OrigenInvalido, TrabajoInexistente

router = APIRouter(
    prefix="/impresion", tags=["impresion"],
    dependencies=[Depends(require_feature("impresion"))],
)

_bearer = HTTPBearer(auto_error=False)


def get_staff_opcional(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal | None:
    """El staff del JWT si viene y es válido; None si no (el dispositivo puede autenticar solo)."""
    if creds is None:
        return None
    try:
        return get_current_user(request, creds)
    except HTTPException:
        return None


def get_validador_dispositivo() -> Callable[[int, str], Awaitable[int | None]]:
    """Puerto inyectable: valida (empresa_id, token) contra el control DB (fake en tests)."""

    async def _validar(empresa_id: int, token: str) -> int | None:
        async with control_session() as cs:
            return await validar_token(cs, empresa_id, token)

    return _validar


async def staff_o_dispositivo(
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    staff: Principal | None = Depends(get_staff_opcional),
    validador: Callable[[int, str], Awaitable[int | None]] = Depends(get_validador_dispositivo),
) -> None:
    """ADR 0033 D6: la superficie de impresión la opera el staff (JWT) O un dispositivo (token).

    El token de dispositivo SOLO existe en este router — jamás autoriza endpoints de negocio.
    """
    if x_device_token:
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None and await validador(tenant.id, x_device_token) is not None:
            return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de dispositivo inválido")
    if staff is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token")
    if not satisface(staff.rol, "vendedor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permisos insuficientes")


def get_impresion_service(session: AsyncSession = Depends(get_tenant_db)) -> ImpresionService:
    return ImpresionService(SqlImpresionRepository(session))


@router.get("/cola", response_model=list[TrabajoLeer])
async def cola(
    svc: ImpresionService = Depends(get_impresion_service),
    _auth: None = Depends(staff_o_dispositivo),
) -> list[TrabajoLeer]:
    """Entrega los trabajos pendientes (y los entregados vencidos) marcándolos `entregado_agente`."""
    return [TrabajoLeer.model_validate(t) for t in await svc.cola()]


@router.post("/trabajos", response_model=TrabajoLeer)
async def crear_trabajo(
    datos: CrearTrabajo,
    svc: ImpresionService = Depends(get_impresion_service),
    _auth: None = Depends(staff_o_dispositivo),
) -> TrabajoLeer:
    """Precuenta/comprobante bajo demanda. Idempotente: repetir el POST devuelve el mismo trabajo."""
    try:
        if datos.tipo == "precuenta":
            trabajo = await svc.crear_precuenta(datos.pedido_id)
        else:
            trabajo = await svc.crear_comprobante(datos.venta_id)
    except OrigenInvalido as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return TrabajoLeer.model_validate(trabajo)


@router.post("/trabajos/{trabajo_id}/ack", response_model=TrabajoLeer)
async def ack(
    trabajo_id: int,
    datos: AckTrabajo,
    svc: ImpresionService = Depends(get_impresion_service),
    _auth: None = Depends(staff_o_dispositivo),
) -> TrabajoLeer:
    try:
        return TrabajoLeer.model_validate(
            await svc.ack(trabajo_id, ok=datos.ok, detalle=datos.detalle)
        )
    except TrabajoInexistente as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trabajo no existe") from e


@router.post("/trabajos/{trabajo_id}/reimprimir", response_model=TrabajoLeer)
async def reimprimir(
    trabajo_id: int,
    svc: ImpresionService = Depends(get_impresion_service),
    _auth: None = Depends(staff_o_dispositivo),
) -> TrabajoLeer:
    try:
        return TrabajoLeer.model_validate(await svc.reimprimir(trabajo_id))
    except TrabajoInexistente as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trabajo no existe") from e


# --- dispositivos (ADR 0033 D6) — SOLO admin con JWT (jamás un dispositivo emite otros) ---------


@router.post("/dispositivos")
async def crear_dispositivo(
    datos: DispositivoCrear,
    request: Request,
    cs: AsyncSession = Depends(get_control_db),
    _admin: Principal = Depends(require_role("admin")),
) -> dict:
    """Emite el token del dispositivo. El texto plano se muestra UNA sola vez, aquí."""
    tenant = request.state.tenant
    dispositivo_id, token = await emitir_dispositivo(cs, tenant.id, datos.nombre)
    return {"id": dispositivo_id, "nombre": datos.nombre, "token": token}


@router.get("/dispositivos")
async def dispositivos(
    request: Request,
    cs: AsyncSession = Depends(get_control_db),
    _admin: Principal = Depends(require_role("admin")),
) -> list[dict]:
    return await listar_dispositivos(cs, request.state.tenant.id)


@router.post("/dispositivos/{dispositivo_id}/revocar")
async def revocar(
    dispositivo_id: int,
    request: Request,
    cs: AsyncSession = Depends(get_control_db),
    _admin: Principal = Depends(require_role("admin")),
) -> dict:
    if not await revocar_dispositivo(cs, request.state.tenant.id, dispositivo_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no existe")
    return {"ok": True}
