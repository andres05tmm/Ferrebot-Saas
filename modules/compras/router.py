"""Router de compras. Pack `pos` (ADR 0008): compras dejó de ser núcleo; sin la capacidad `pos`, todo
el router responde 404.

Registrar/listar compras es solo de admin (RBAC). Lo fiscal (compras_fiscal/RADIAN) va gateado y es de
otro slice. La lógica vive en ComprasService; aquí solo se valida y se mapea a HTTP.
"""
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import get_capacidades, require_feature
from core.db.session import get_tenant_db
from modules.compras.errors import IdempotenciaConflicto
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import AnalisisPrecioProveedor, CompraCrear, CompraLeer
from modules.compras.service import ComprasService, RetencionesAplicador
from modules.retenciones.repository import SqlRetencionesRepository
from modules.retenciones.service import RetencionesService

router = APIRouter(tags=["compras"], dependencies=[Depends(require_feature("inventario"))])


def _aplicador_retenciones(
    session: AsyncSession, capacidades: frozenset[str]
) -> RetencionesAplicador | None:
    """RetencionesService atado a la sesión del tenant SOLO si tiene la feature `retenciones`; None si
    no (el motor no corre para tenants que no lo activaron)."""
    if "retenciones" not in capacidades:
        return None
    return RetencionesService(SqlRetencionesRepository(session))


def _service(
    session: AsyncSession, capacidades: frozenset[str] = frozenset()
) -> ComprasService:
    return ComprasService(
        SqlComprasRepository(session),
        retenciones=_aplicador_retenciones(session, capacidades),
    )


@router.post("/compras", response_model=CompraLeer, status_code=status.HTTP_201_CREATED)
async def crear_compra(
    payload: CompraCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
    capacidades: frozenset[str] = Depends(get_capacidades),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CompraLeer:
    """Registra una compra: suma stock (ENTRADA) y fija el costo de compra de cada producto.

    Idempotente por `Idempotency-Key` (ai-tools.md §4): reintento con la misma key y mismo payload →
    200 con la compra original (no duplica); misma key con payload distinto → 409. Si el tenant tiene
    la feature `retenciones`, calcula y persiste las retenciones practicadas al proveedor (ADR 0027).
    """
    if payload.idempotency_key is None and idempotency_key:
        payload = payload.model_copy(update={"idempotency_key": idempotency_key})
    try:
        resultado = await _service(session, capacidades).registrar(payload, usuario_id=user.user_id)
    except IdempotenciaConflicto as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if resultado.replay:
        response.status_code = status.HTTP_200_OK  # idempotencia: ya existía
    return resultado.compra


@router.get("/compras/resbalos", response_model=list[CompraLeer])
async def reporte_resbalos(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[CompraLeer]:
    """Reporte de resbalos (spec 11): viajes de material del rango con margen $ y % + alerta de baja
    rentabilidad. Ordenado del mayor margen al menor (default mes en curso, hora Colombia)."""
    return await _service(session).reporte_resbalos(desde=desde, hasta=hasta)


@router.get("/compras/analisis-precios", response_model=list[AnalisisPrecioProveedor])
async def analisis_precios_proveedor(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    proveedor_id: int | None = Query(default=None, description="Filtra por proveedor"),
    categoria: str | None = Query(default=None, description="Filtra por categoría de compra"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[AnalisisPrecioProveedor]:
    """Análisis de precios de proveedor (spec 10): costo unitario ponderado por (proveedor, categoría) del
    período con su rango y alerta de sobreprecio (>15% sobre el promedio del proveedor). Vista de solo
    lectura para vigilar sobreprecios. Default: últimos 6 meses (hora Colombia). Ordenado del más caro."""
    return await _service(session).analisis_precios(
        desde=desde, hasta=hasta, proveedor_id=proveedor_id, categoria=categoria,
    )


@router.get("/compras", response_model=list[CompraLeer])
async def listar_compras(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[CompraLeer]:
    """Historial de compras del rango (default mes en curso, hora Colombia)."""
    return await _service(session).listar(desde=desde, hasta=hasta)
