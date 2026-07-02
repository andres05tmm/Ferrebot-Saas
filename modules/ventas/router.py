"""Router de ventas: valida, resuelve permisos y delega en el servicio (sin lógica de negocio).

POST /ventas es idempotente (header Idempotency-Key). GET /ventas lista el historial del rango
(scopeado por vendedor). GET /events es el stream SSE de la empresa.
"""
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from core.auth import Principal, get_current_user, get_filtro_efectivo, require_role
from core.auth.features import get_capacidades, require_feature
from core.auth.rbac import satisface
from core.db.session import control_session, get_tenant_db
from core.events.sse import tenant_event_stream
from modules.facturacion.repository import SqlFacturacionRepository
from modules.ventas.config import cargar_control_stock_estricto
from modules.ventas.errors import (
    LineaInvalida,
    OperacionNoAutorizada,
    ProductoNoEncontrado,
    StockInsuficiente,
    VentaConFacturaViva,
    VentaNoEncontrada,
    VentaNoEsDeHoy,
)
from modules.facturacion.pos_hook import encolar_cierre_pos
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaConLineas, VentaCrear, VentaLeer
from modules.ventas.service import VentaService

# Feature fina `ventas` (ADR 0021, antes pack `pos`): sin la capacidad, todo el router responde 404.
router = APIRouter(tags=["ventas"], dependencies=[Depends(require_feature("ventas"))])


def get_ventas_repo(session: AsyncSession = Depends(get_tenant_db)) -> SqlVentasRepository:
    """Repo de ventas sobre la sesión del tenant para las lecturas (overridable en test)."""
    return SqlVentasRepository(session)


def get_facturacion_lectura(session: AsyncSession = Depends(get_tenant_db)) -> SqlFacturacionRepository:
    """Repo de facturación (misma sesión del tenant) solo para componer el badge fiscal (overridable)."""
    return SqlFacturacionRepository(session)


# Capacidades que habilitan el estado fiscal de una venta (sin alguna → no se consulta la tabla).
FEATURES_FISCAL = ("pos_electronico", "facturacion_electronica")


async def _componer_fiscal(
    ventas: list[VentaLeer], fact_repo: SqlFacturacionRepository, capacidades: frozenset[str],
) -> list[VentaLeer]:
    """Adjunta el estado fiscal (badge) a cada venta en UN solo batch (sin N+1), sobre el resultado del repo.

    Sin capacidad fiscal NO consulta `facturas_electronicas` y deja `fiscal=None`; con capacidad, lee los
    estados de TODA la lista en una query y devuelve copias con `fiscal` poblado (las ventas sin documento
    quedan en None). El SQL vive en el repo: el router solo compone."""
    if not ventas or not any(f in capacidades for f in FEATURES_FISCAL):
        return ventas
    estados = await fact_repo.estados_por_ventas([v.id for v in ventas])
    return [v.model_copy(update={"fiscal": estados.get(v.id)}) for v in ventas]


async def get_control_stock_estricto(request: Request) -> bool:
    """Flag de control de stock estricto de la empresa resuelta (control DB per-call; overridable en test).

    Patrón de `get_facturacion_service`: lee del control DB sobre una sesión per-call. Default PERMISIVO:
    si no hay empresa resuelta (apps mínimas de test sin TenantMiddleware) → False.
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return False
    async with control_session() as cs:
        return await cargar_control_stock_estricto(cs, tenant.id)


@router.post("/ventas", response_model=VentaLeer, status_code=status.HTTP_201_CREATED)
async def crear_venta(
    payload: VentaCrear,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    control_stock_estricto: bool = Depends(get_control_stock_estricto),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> VentaLeer:
    if payload.idempotency_key is None and idempotency_key:
        payload = payload.model_copy(update={"idempotency_key": idempotency_key})

    service = VentaService(SqlVentasRepository(session))
    try:
        resultado = await service.registrar_venta(
            payload, vendedor_id=user.user_id, control_stock_estricto=control_stock_estricto
        )
    except ProductoNoEncontrado as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except StockInsuficiente as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LineaInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Cierre fiscal de mostrador (ADR 0012 D2): si la empresa tiene `pos_electronico`, crea el pendiente
    # POS, lo COMMITEA y encola la emisión. Solo en venta NUEVA (en replay el cierre ya ocurrió).
    # Idempotente y excluyente con la FE (D1); nunca rompe la venta.
    if not resultado.replay:
        await encolar_cierre_pos(request, session, resultado.venta.id, intencion=payload.documento)
    else:
        response.status_code = status.HTTP_200_OK  # idempotencia: ya existía
    return resultado.venta


@router.get("/ventas", response_model=list[VentaLeer])
async def listar_ventas(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    repo: SqlVentasRepository = Depends(get_ventas_repo),
    fact_repo: SqlFacturacionRepository = Depends(get_facturacion_lectura),
    capacidades: frozenset[str] = Depends(get_capacidades),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> list[VentaLeer]:
    """Historial del rango (default = hoy Colombia); el vendedor efectivo lo decide el filtro RBAC.

    Compone el estado fiscal (badge) de toda la lista en un solo batch si el tenant tiene capacidad fiscal."""
    ventas = await repo.listar(desde=desde, hasta=hasta, vendedor_id=filtro)
    return await _componer_fiscal(ventas, fact_repo, capacidades)


@router.get("/ventas/{venta_id}", response_model=VentaConLineas)
async def obtener_venta(
    venta_id: int,
    repo: SqlVentasRepository = Depends(get_ventas_repo),
    fact_repo: SqlFacturacionRepository = Depends(get_facturacion_lectura),
    capacidades: frozenset[str] = Depends(get_capacidades),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> VentaConLineas:
    """Detalle de una venta con sus líneas, acotado al vendedor efectivo: si no existe o no es suya
    → 404 (mismo mensaje, para no revelar la existencia de ventas de otro vendedor). Lleva el estado
    fiscal (badge + CUDE/CUFE) si el tenant tiene capacidad fiscal."""
    venta = await repo.obtener(venta_id)
    if venta is None or (filtro is not None and venta.vendedor_id != filtro):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Venta {venta_id} no existe")
    (compuesta,) = await _componer_fiscal([venta], fact_repo, capacidades)
    return compuesta


@router.put("/ventas/{venta_id}", response_model=VentaConLineas)
async def editar_venta(
    venta_id: int,
    payload: VentaCrear,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(get_current_user),
    control_stock_estricto: bool = Depends(get_control_stock_estricto),
) -> VentaConLineas:
    """Edita una venta de HOY EN EL LUGAR (mismo id/consecutivo/fecha). Permiso: admin o el dueño.

    404 si no existe; 409 si no es del día o tiene factura electrónica viva; 403 si un vendedor edita
    la de otro; 404/422 si una línea nueva referencia un producto inexistente o es inválida. El servicio
    revierte el stock viejo y aplica el nuevo en una transacción y emite `venta_editada`.
    """
    service = VentaService(SqlVentasRepository(session))
    try:
        return await service.editar_venta(
            venta_id, payload, user_id=user.user_id,
            es_admin=satisface(user.rol, "admin"), control_stock_estricto=control_stock_estricto,
        )
    except VentaNoEncontrada as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except OperacionNoAutorizada as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (VentaNoEsDeHoy, VentaConFacturaViva, StockInsuficiente) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProductoNoEncontrado as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except LineaInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.delete("/ventas/{venta_id}")
async def borrar_venta(
    venta_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(get_current_user),
) -> dict[str, object]:
    """Borra una venta de HOY (Colombia) restaurando stock. Permiso: admin o el vendedor dueño.

    404 si no existe; 409 si no es del día o tiene factura electrónica viva; 403 si un vendedor
    intenta borrar la venta de otro. El borrado físico (revierte stock + movimientos) lo hace el
    servicio en una transacción y emite `venta_anulada` + `inventario_actualizado`.
    """
    service = VentaService(SqlVentasRepository(session))
    try:
        await service.borrar_venta(
            venta_id, user_id=user.user_id, es_admin=satisface(user.rol, "admin")
        )
    except VentaNoEncontrada as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except OperacionNoAutorizada as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (VentaNoEsDeHoy, VentaConFacturaViva) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"venta_id": venta_id, "borrada": True}


@router.get("/events")
async def events(
    request: Request,
    _user: Principal = Depends(get_current_user),
) -> EventSourceResponse:
    return EventSourceResponse(tenant_event_stream(request.state.tenant))
