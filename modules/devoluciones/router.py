"""Router de devoluciones: valida, resuelve permisos y delega en el servicio (ADR 0026).

POST /devoluciones es idempotente (header Idempotency-Key o campo del payload; replay → 200). La
composición carga las credenciales MATIAS del control DB SOLO si hay tenant resuelto: sin ellas la
devolución sale igual y la nota crédito (si la venta fue facturada) queda en `error` reintentable —
la emisión nunca bloquea el reintegro. Feature fina `ventas` (misma superficie que el POS).
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Query

from core.auth import Principal, get_filtro_efectivo, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from core.logging import get_logger
from modules.caja.repository import SqlCajaRepository
from modules.devoluciones.errors import (
    CajaRequerida,
    DevolucionConflicto,
    DevolucionExcedeVenta,
    FiadoNoEncontrado,
    LineaNoVendida,
    NadaPorDevolver,
    VentaNoEncontrada,
)
from modules.devoluciones.repository import SqlDevolucionesRepository
from modules.devoluciones.schemas import DevolucionCrear, DevolucionLeer, VentaFacturadaLeer
from modules.devoluciones.service import DevolucionesService
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.matias_client import MatiasClient
from modules.facturacion.notas import NotasService, SqlNotasRepository
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService

log = get_logger("devoluciones.router")

router = APIRouter(tags=["devoluciones"], dependencies=[Depends(require_feature("ventas"))])


async def get_devoluciones_service(
    request: Request, session: AsyncSession = Depends(get_tenant_db)
) -> DevolucionesService:
    """Compone el servicio sobre la sesión del tenant (overridable en test).

    MATIAS/config se cargan del control DB si hay tenant resuelto; si faltan credenciales (tenant sin
    capacidad fiscal, apps mínimas de test) la nota se crea igual y queda `error` reintentable — el
    reintegro de la devolución NUNCA depende de que MATIAS responda."""
    matias, config = None, None
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        try:
            async with control_session() as cs:
                cred, config = await cargar_config_matias(
                    cs, get_settings().secrets_master_key, tenant.id
                )
            matias = MatiasClient(cred)
        except Exception:  # noqa: BLE001 — sin credenciales: la nota queda error reintentable
            log.warning("devoluciones_sin_config_matias", tenant_id=tenant.id, exc_info=True)
    notas = NotasService(SqlNotasRepository(session), matias, config)
    return DevolucionesService(
        SqlDevolucionesRepository(session),
        caja=SqlCajaRepository(session),
        fiados=FiadosService(SqlFiadosRepository(session)),
        notas=notas,
    )


def get_devoluciones_repo(session: AsyncSession = Depends(get_tenant_db)) -> SqlDevolucionesRepository:
    """Repo de devoluciones sobre la sesión del tenant, para las lecturas (overridable en test)."""
    return SqlDevolucionesRepository(session)


@router.get("/devoluciones/ventas-facturadas", response_model=list[VentaFacturadaLeer])
async def listar_ventas_facturadas(
    q: str | None = Query(default=None),
    limite: int = Query(default=20, ge=1, le=50),
    repo: SqlDevolucionesRepository = Depends(get_devoluciones_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> list[VentaFacturadaLeer]:
    """Ventas con documento fiscal vivo (POS/FE) para emitir nota crédito: las más recientes, o las que
    matcheen `q` (número de venta O CUFE). Acotadas al vendedor efectivo (RBAC)."""
    return await repo.listar_ventas_facturadas(q=q, limite=limite, vendedor_id=filtro)


@router.post("/devoluciones", response_model=DevolucionLeer, status_code=status.HTTP_201_CREATED)
async def crear_devolucion(
    payload: DevolucionCrear,
    response: Response,
    service: DevolucionesService = Depends(get_devoluciones_service),
    user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> DevolucionLeer:
    """Registra la devolución (total si no trae líneas; parcial si las trae). Idempotente.

    404 venta inexistente; 409 sin caja abierta / sin fiado / sobre-devolución / nada por devolver /
    key reusada con otro payload; 422 línea que la venta no incluye."""
    if payload.idempotency_key is None and idempotency_key:
        payload = payload.model_copy(update={"idempotency_key": idempotency_key})
    try:
        resultado = await service.devolver(payload, usuario_id=user.user_id)
    except VentaNoEncontrada as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (
        CajaRequerida, FiadoNoEncontrado, DevolucionConflicto,
        DevolucionExcedeVenta, NadaPorDevolver,
    ) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LineaNoVendida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if resultado.replay:
        response.status_code = status.HTTP_200_OK  # idempotencia: ya existía
    return DevolucionLeer.model_validate(resultado.devolucion)
