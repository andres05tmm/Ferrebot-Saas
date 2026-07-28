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
from core.config.timezone import rango_dia_co, today_co
from core.db.session import control_session, get_tenant_db
from core.events.sse import tenant_event_stream
from core.tenancy.catalogo import expandir_metapacks
from modules.caja.config import get_caja_obligatoria
from modules.caja.repository import SqlCajaRepository
from modules.facturacion.repository import SqlFacturacionRepository
from modules.reportes.repository import SqlReportesRepository
from modules.ventas.config import cargar_control_stock_estricto
from modules.fiados.errors import ClienteInexistente
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.ventas.errors import (
    IdempotenciaConflicto,
    LineaInvalida,
    OperacionNoAutorizada,
    ProductoNoEncontrado,
    StockInsuficiente,
    VentaConDevolucion,
    VentaConFacturaViva,
    VentaNoEncontrada,
    VentaNoEsDeHoy,
)
from modules.facturacion.pos_hook import encolar_cierre_pos
from modules.retenciones.repository import SqlRetencionesRepository
from modules.retenciones.service import RetencionesService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import (
    HistorialFeed,
    VentaConLineas,
    VentaCrear,
    VentaLeer,
    VentaRecienteLeer,
)
from modules.ventas.service import RetencionesAplicador, VentaService

# Feature fina `ventas` (ADR 0021, antes pack `pos`): sin la capacidad, todo el router responde 404.
router = APIRouter(tags=["ventas"], dependencies=[Depends(require_feature("ventas"))])


# Un trimestre. Sin esta cota, `?desde=2020-01-01` es un recorrido completo de `ventas` unida a
# `ventas_detalle`. Va como dependencia y no dentro del endpoint porque el EXPORT tiene que aplicar
# exactamente el mismo límite, y el export es justo el que se olvida.
RANGO_HISTORIAL_MAX_DIAS = 92

# Tope del archivo. Por encima de esto el export responde 422 pidiendo partir por meses, en vez de
# entregar un Excel incompleto que nadie puede detectar como incompleto.
LIMITE_EXPORT_VENTAS = 5_000

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def rango_historial(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
) -> tuple[date, date]:
    """Valida y normaliza el rango del historial (default: hoy Colombia)."""
    hoy = today_co()
    d, h = desde or hoy, hasta or hoy
    if h < d:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "La fecha final es anterior a la inicial"
        )
    if (h - d).days > RANGO_HISTORIAL_MAX_DIAS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"El rango no puede superar {RANGO_HISTORIAL_MAX_DIAS} días; consulta por meses",
        )
    return d, h


def _aplicador_retenciones(
    session: AsyncSession, capacidades: frozenset[str]
) -> RetencionesAplicador | None:
    """RetencionesService atado a la sesión del tenant SOLO si tiene la feature `retenciones`; None si
    no (así el motor no corre —ni una consulta— para tenants que no lo activaron)."""
    if "retenciones" not in capacidades:
        return None
    return RetencionesService(SqlRetencionesRepository(session))


def get_ventas_repo(session: AsyncSession = Depends(get_tenant_db)) -> SqlVentasRepository:
    """Repo de ventas sobre la sesión del tenant para las lecturas (overridable en test)."""
    return SqlVentasRepository(session)


def get_reportes_lectura(session: AsyncSession = Depends(get_tenant_db)) -> SqlReportesRepository:
    """Repo de reportes (misma sesión del tenant) solo para el total del pie del export."""
    return SqlReportesRepository(session)


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
    caja_obligatoria: bool = Depends(get_caja_obligatoria),
    capacidades: frozenset[str] = Depends(get_capacidades),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> VentaLeer:
    # Guard de caja (modo un-cajón por empresa, opt-in `caja_obligatoria`): sin una caja abierta EN LA
    # EMPRESA no se registra la venta. Corre ANTES del servicio: el 409 no consume la Idempotency-Key,
    # así el POS puede abrir caja y reintentar el MISMO payload sin repetir nada. Solo si el tenant
    # tiene la capacidad `caja` (default seguro: el toggle sin la feature no bloquea la venta).
    if (
        caja_obligatoria
        and "caja" in expandir_metapacks(capacidades)
        and await SqlCajaRepository(session).caja_abierta_empresa() is None
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "caja_no_abierta",
                "mensaje": "Abre la caja antes de registrar la primera venta del día.",
            },
        )
    if payload.idempotency_key is None and idempotency_key:
        payload = payload.model_copy(update={"idempotency_key": idempotency_key})

    service = VentaService(
        SqlVentasRepository(session),
        fiados=FiadosService(SqlFiadosRepository(session)),
        # Retenciones inline (ADR 0027), solo si el tenant tiene la feature `retenciones`: calcula y
        # persiste los renglones tributarios de la venta en su misma transacción (opt-in).
        retenciones=_aplicador_retenciones(session, capacidades),
    )
    try:
        resultado = await service.registrar_venta(
            payload, vendedor_id=user.user_id, control_stock_estricto=control_stock_estricto
        )
    except ProductoNoEncontrado as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (StockInsuficiente, IdempotenciaConflicto) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (LineaInvalida, ClienteInexistente) as exc:
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


@router.get("/ventas/recientes", response_model=list[VentaRecienteLeer])
async def listar_ventas_recientes(
    limite: int = Query(default=5, ge=1, le=20),
    repo: SqlVentasRepository = Depends(get_ventas_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> list[VentaRecienteLeer]:
    """Feed 'Últimas ventas' del cockpit: las N ventas completadas más recientes con sus items
    (nombre+cantidad) resueltos, acotadas al vendedor efectivo (RBAC). DEBE declararse antes de
    `/ventas/{venta_id}` para que 'recientes' no matchee el parámetro de path."""
    return await repo.listar_recientes(limite=limite, vendedor_id=filtro)


@router.get("/ventas/historial", response_model=HistorialFeed)
async def historial_ventas(
    rango: tuple[date, date] = Depends(rango_historial),
    limite: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    repo: SqlVentasRepository = Depends(get_ventas_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> HistorialFeed:
    """El libro de ventas: una fila por RENGLÓN vendido, con producto, cliente y vendedor resueltos.

    `/ventas` devuelve cabeceras y esconde los productos; esto es lo contrario, y es lo que el dueño
    mira a diario. `limite` cuenta VENTAS, no renglones: una página nunca parte una venta por la
    mitad. Acotado al vendedor efectivo por RBAC.

    DEBE declararse antes de `/ventas/{venta_id}`, o 'historial' matchea el parámetro de path y
    responde 422 (ya pasó con `/ventas/recientes`)."""
    desde, hasta = rango
    return await repo.historial_lineas(
        desde=desde, hasta=hasta, vendedor_id=filtro, limite=limite, offset=offset
    )


@router.get("/ventas/historial/exportar")
async def exportar_historial(
    rango: tuple[date, date] = Depends(rango_historial),
    repo: SqlVentasRepository = Depends(get_ventas_repo),
    reportes: SqlReportesRepository = Depends(get_reportes_lectura),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> Response:
    """El libro de ventas del rango como `.xlsx`.

    Mismo rango y **mismo filtro por rol** que el feed: el export es justo el endpoint donde se
    olvida el aislamiento y un vendedor termina bajándose el Excel de toda la tienda.

    El TOTAL del pie sale de `/reportes/resumen` (suma de cabeceras, sin anuladas), no de sumar los
    renglones del propio archivo: ver el aviso de `services/export/historial_ventas.py`.
    """
    desde, hasta = rango
    feed = await repo.historial_lineas(
        desde=desde, hasta=hasta, vendedor_id=filtro, limite=LIMITE_EXPORT_VENTAS
    )
    if feed.hay_mas:
        # Nada de truncar en silencio: un Excel con la mitad de las ventas del mes es peor que un
        # error, porque el que lo abre no tiene cómo notar lo que falta.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"El rango supera las {LIMITE_EXPORT_VENTAS} ventas por archivo; expórtalo por meses",
        )
    inicio, fin = rango_dia_co(desde, hasta)
    agg = await reportes.resumen(inicio=inicio, fin=fin, vendedor_id=filtro)

    from services.export.historial_ventas import render_historial_excel   # perezoso: openpyxl

    xlsx = render_historial_excel(
        feed.filas, desde=desde, hasta=hasta, total_periodo=agg.total_vendido
    )
    return Response(
        content=xlsx,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="ventas_{desde}_{hasta}.xlsx"'},
    )


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
    except (VentaNoEsDeHoy, VentaConFacturaViva, VentaConDevolucion, StockInsuficiente) as exc:
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
    except (VentaNoEsDeHoy, VentaConFacturaViva, VentaConDevolucion) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"venta_id": venta_id, "borrada": True}


@router.get("/events")
async def events(
    request: Request,
    _user: Principal = Depends(get_current_user),
) -> EventSourceResponse:
    return EventSourceResponse(tenant_event_stream(request.state.tenant))
