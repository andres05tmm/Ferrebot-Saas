"""Router del cotizador AIU (vertical construcción, contrato Ola A §3.1).

Gateado por la capacidad `cotizaciones_aiu`: sin ella el router entero responde 404. Prefijo de rutas
`/cotizaciones-obra` (el POS ya ocupa `/cotizaciones`). RBAC (contrato §3.1): las lecturas, el alta, la
edición y la transición son de `vendedor`; **convertir a obra es de `admin`** (crea el activo obra). Los
totales AIU SIEMPRE salen de la función pura (vía el servicio); aquí sólo se valida, se mapea a HTTP y
se serializa.

El servicio se inyecta por dependencia sobre la sesión del tenant: usa su propio repositorio y, para la
conversión, el `ObrasService` de `modules.obra` (método aditivo `crear_desde_cotizacion`) sobre la MISMA
sesión (misma transacción). Los tests lo overridean con un fake (sin red ni Postgres).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.cotizacion_obra.errors import (
    CotizacionInexistente,
    CotizacionNoEditable,
    CotizacionNoGanada,
    NumeroDuplicado,
    TransicionEstadoInvalida,
)
from modules.cotizacion_obra.repository import SqlCotizacionObraRepository
from modules.cotizacion_obra.schemas import (
    CotizacionObraActualizar,
    CotizacionObraCrear,
    CotizacionObraEstadoCambiar,
    CotizacionObraLeer,
    CotizacionObraResumen,
    EstadoCotizacion,
    ItemCotizacionObraLeer,
    TotalesAIULeer,
)
from modules.cotizacion_obra.service import CotizacionArmada, CotizacionObraService
from modules.obra.repository import SqlObrasRepository
from modules.obra.schemas import ObraLeer
from modules.obra.service import ObrasService

router = APIRouter(
    tags=["cotizaciones-obra"],
    dependencies=[Depends(require_feature("cotizaciones_aiu"))],
)

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def get_cotizacion_obra_service(
    session: AsyncSession = Depends(get_tenant_db),
) -> CotizacionObraService:
    """Arma el servicio sobre la sesión del tenant: su repo + el `ObrasService` para la conversión."""
    return CotizacionObraService(
        SqlCotizacionObraRepository(session),
        ObrasService(SqlObrasRepository(session)),
    )


def _a_leer(armada: CotizacionArmada) -> CotizacionObraLeer:
    """Mapea una `CotizacionArmada` a su vista de lectura (ítems con subtotal + desglose AIU)."""
    c = armada.cotizacion
    items = [
        ItemCotizacionObraLeer(
            id=it.id,
            orden=it.orden,
            descripcion=it.descripcion,
            unidad=it.unidad,
            cantidad=it.cantidad,
            valor_unitario=it.valor_unitario,
            subtotal=it.cantidad * it.valor_unitario,
            costo_material_est=it.costo_material_est,
            costo_mano_obra_est=it.costo_mano_obra_est,
            costo_equipo_est=it.costo_equipo_est,
        )
        for it in armada.items
    ]
    return CotizacionObraLeer(
        id=c.id,
        numero=c.numero,
        cliente_id=c.cliente_id,
        nombre_obra=c.nombre_obra,
        ubicacion=c.ubicacion,
        fecha_emision=c.fecha_emision,
        vigencia_dias=c.vigencia_dias,
        administracion_pct=c.administracion_pct,
        imprevistos_pct=c.imprevistos_pct,
        utilidad_pct=c.utilidad_pct,
        iva_sobre_utilidad_pct=c.iva_sobre_utilidad_pct,
        estado=c.estado,
        condiciones=c.condiciones,
        creado_en=c.creado_en,
        actualizado_en=c.actualizado_en,
        items=items,
        totales=TotalesAIULeer.model_validate(armada.totales),
    )


def _a_resumen(armada: CotizacionArmada, *, cliente_nombre: str | None = None) -> CotizacionObraResumen:
    c = armada.cotizacion
    return CotizacionObraResumen(
        id=c.id,
        numero=c.numero,
        cliente_id=c.cliente_id,
        cliente_nombre=cliente_nombre,
        nombre_obra=c.nombre_obra,
        ubicacion=c.ubicacion,
        fecha_emision=c.fecha_emision,
        vigencia_dias=c.vigencia_dias,
        estado=c.estado,
        creado_en=c.creado_en,
        actualizado_en=c.actualizado_en,
        total=armada.totales.total,
    )


@router.get("/cotizaciones-obra", response_model=list[CotizacionObraResumen])
async def listar_cotizaciones(
    estado: EstadoCotizacion | None = Query(default=None, description="Filtra por estado"),
    cliente_id: int | None = Query(default=None, description="Filtra por cliente"),
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[CotizacionObraResumen]:
    """Cotizaciones (más recientes primero), filtrables por estado y cliente. Con el NOMBRE del
    cliente resuelto en lote (F2.9): la lista debe decir a quién se cotizó, no un id."""
    armadas = await service.listar(estado=estado, cliente_id=cliente_id)
    nombres = await service.nombres_clientes([a.cotizacion.cliente_id for a in armadas])
    return [_a_resumen(a, cliente_nombre=nombres.get(a.cotizacion.cliente_id)) for a in armadas]


@router.post(
    "/cotizaciones-obra",
    response_model=CotizacionObraLeer,
    status_code=status.HTTP_201_CREATED,
)
async def crear_cotizacion(
    payload: CotizacionObraCrear,
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CotizacionObraLeer:
    """Crea un borrador (número `PIM-0XX-AAAA` autogenerado si no se envía). Totales por la función pura."""
    try:
        armada = await service.crear(payload)
    except NumeroDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _a_leer(armada)


@router.get("/cotizaciones-obra/{cotizacion_id}", response_model=CotizacionObraLeer)
async def obtener_cotizacion(
    cotizacion_id: int,
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CotizacionObraLeer:
    """Detalle: cabecera + ítems (con subtotal) + desglose AIU. 404 si no existe."""
    try:
        armada = await service.obtener(cotizacion_id)
    except CotizacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _a_leer(armada)


@router.put("/cotizaciones-obra/{cotizacion_id}", response_model=CotizacionObraLeer)
async def actualizar_cotizacion(
    cotizacion_id: int,
    payload: CotizacionObraActualizar,
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CotizacionObraLeer:
    """Edita el builder (cabecera + ítems). 404 si no existe; 409 si el estado no admite edición."""
    try:
        armada = await service.actualizar(cotizacion_id, payload)
    except CotizacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CotizacionNoEditable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _a_leer(armada)


@router.post("/cotizaciones-obra/{cotizacion_id}/estado", response_model=CotizacionObraLeer)
async def cambiar_estado_cotizacion(
    cotizacion_id: int,
    payload: CotizacionObraEstadoCambiar,
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CotizacionObraLeer:
    """Marca ENVIADA/GANADA/PERDIDA/VENCIDA. 404 si no existe; 409 si la transición no se permite."""
    try:
        armada = await service.cambiar_estado(cotizacion_id, payload.estado)
    except CotizacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TransicionEstadoInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _a_leer(armada)


@router.get("/cotizaciones-obra/{cotizacion_id}/exportar-excel")
async def exportar_excel(
    cotizacion_id: int,
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> Response:
    """Descarga el `.xlsx` de la cotización (formato PROVISIONAL). 404 si no existe.

    El motor (`services.export.cotizacion`) se importa perezosamente: openpyxl sólo se carga al exportar.
    """
    try:
        armada = await service.obtener(cotizacion_id)
    except CotizacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    from services.export.cotizacion import EmpresaCotizacion, render_cotizacion_excel

    xlsx = render_cotizacion_excel(
        armada.cotizacion, armada.items, armada.totales, EmpresaCotizacion()
    )
    nombre_archivo = f"{armada.cotizacion.numero}.xlsx"
    return Response(
        content=xlsx,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


@router.post("/cotizaciones-obra/{cotizacion_id}/convertir-obra", response_model=ObraLeer)
async def convertir_a_obra(
    cotizacion_id: int,
    service: CotizacionObraService = Depends(get_cotizacion_obra_service),
    _user: Principal = Depends(require_role("admin")),
) -> ObraLeer:
    """Convierte una cotización GANADA en Obra (PLANIFICADA, 1-1). 404 si no existe; 409 si no está GANADA.

    Idempotente: si la cotización ya se convirtió, devuelve la MISMA obra (no crea una segunda).
    """
    try:
        obra = await service.convertir_a_obra(cotizacion_id)
    except CotizacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CotizacionNoGanada as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ObraLeer.model_validate(obra)
