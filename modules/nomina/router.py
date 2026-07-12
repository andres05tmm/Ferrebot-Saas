"""Router de nómina (`/nomina/*`, vertical construcción). Gateado por la capacidad `nomina` (404 sin el
flag). RBAC = admin: la nómina es dato sensible (salarios, aportes); solo el registro de asistencia es
de rol vendedor (captura de campo). Excluido de la Ola A: `/transmitir-dian` (CUNE) → Fase 7.

La lógica vive en `NominaService`; aquí solo se resuelve la sesión del tenant, se valida el rol, se
mapea a HTTP y se serializan los DTO (componiendo nombre de trabajador/obra, como `modules.contabilidad`).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.nomina.errors import (
    ParametrosLegalesInexistentes,
    PeriodoBloqueado,
    PeriodoNominaInexistente,
    PeriodoSolapado,
    TrabajadorNoLiquidable,
)
from modules.nomina.models import DetalleLiquidacion, PeriodoNomina, ProrrateoNominaObra
from modules.nomina.repository import SqlNominaRepository
from modules.nomina.schemas import (
    AccionResultado,
    AsistenciaCrear,
    AsistenciaLeer,
    DetalleLiquidacionLeer,
    LiquidacionResultado,
    ParametrosSnapshot,
    PeriodoCrear,
    PeriodoDetalle,
    PeriodoLeer,
    ProrrateoLeer,
    TotalesPeriodo,
    TrabajadorLiquidacion,
    TransmisionEncolada,
)
from modules.nomina.service import NominaService
from modules.trabajadores.models import Trabajador

router = APIRouter(
    prefix="/nomina", tags=["nomina"], dependencies=[Depends(require_feature("nomina"))]
)

_CERO = Decimal("0")


def get_nomina_service(session: AsyncSession = Depends(get_tenant_db)) -> NominaService:
    """Arma el `NominaService` sobre la sesión del tenant (los tests lo overridean con un fake)."""
    return NominaService(SqlNominaRepository(session))


class Enqueuer(Protocol):
    """Puerto de cola ARQ (mismo contrato que `facturacion.router.Enqueuer`); en tests, un fake."""

    async def enqueue(self, job: str, *args) -> None: ...


class _ArqEnqueuer:
    """Adaptador sobre el pool ARQ del lifespan del API: `enqueue(job, *args)` → `enqueue_job`."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def enqueue(self, job: str, *args) -> None:
        await self._pool.enqueue_job(job, *args)


async def get_enqueuer(request: Request) -> Enqueuer:
    """Encolador sobre el pool ARQ creado en el lifespan del API (`app.state.arq_pool`; overridable en test)."""
    return _ArqEnqueuer(request.app.state.arq_pool)


def get_tenant_id(request: Request) -> int:
    """tenant_id de la empresa resuelta por el TenantMiddleware (overridable en test)."""
    return request.state.tenant.id


# --- mapeo ORM → DTO ---------------------------------------------------------
def _periodo_leer(p: PeriodoNomina) -> PeriodoLeer:
    return PeriodoLeer(
        id=p.id, nombre=p.nombre, tipo=p.tipo, fecha_inicio=p.fecha_inicio, fecha_fin=p.fecha_fin,
        estado=p.estado, liquidado_en=p.liquidado_en, pagado_en=p.pagado_en, creado_en=p.creado_en,
    )


def _snapshot(p: PeriodoNomina) -> ParametrosSnapshot:
    return ParametrosSnapshot(
        smmlv=p.param_smmlv, auxilio_transporte=p.param_auxilio_transporte,
        auxilio_transporte_tope_smmlv=p.param_auxilio_transporte_tope_smmlv,
        horas_mes=p.param_horas_mes, recargo_he_diurna=p.param_recargo_he_diurna,
        recargo_he_nocturna=p.param_recargo_he_nocturna, recargo_dominical=p.param_recargo_dominical,
        salud_empleado_pct=p.param_salud_empleado_pct, pension_empleado_pct=p.param_pension_empleado_pct,
        salud_empleador_pct=p.param_salud_empleador_pct, pension_empleador_pct=p.param_pension_empleador_pct,
        arl_pct=p.param_arl_pct, caja_compensacion_pct=p.param_caja_compensacion_pct,
        sena_pct=p.param_sena_pct, icbf_pct=p.param_icbf_pct, cesantias_pct=p.param_cesantias_pct,
        intereses_cesantias_pct=p.param_intereses_cesantias_pct, prima_pct=p.param_prima_pct,
        vacaciones_pct=p.param_vacaciones_pct,
    )


def _detalle_leer(d: DetalleLiquidacion, t: Trabajador | None) -> DetalleLiquidacionLeer:
    return DetalleLiquidacionLeer(
        id=d.id, trabajador_id=d.trabajador_id,
        trabajador_nombre=f"{t.nombres} {t.apellidos}" if t else f"Trabajador #{d.trabajador_id}",
        trabajador_documento=t.documento if t else "—",
        tipo_vinculacion=d.tipo_vinculacion, dias_liquidados=d.dias_liquidados,
        salario_devengado=d.salario_devengado, auxilio_transporte=d.auxilio_transporte,
        valor_horas_extra=d.valor_horas_extra, total_devengado=d.total_devengado,
        salud_empleado=d.salud_empleado, pension_empleado=d.pension_empleado,
        total_deducciones=d.total_deducciones, neto_pagar=d.neto_pagar,
        aportes_empleador=d.aportes_empleador, provisiones=d.provisiones,
        costo_total=d.total_devengado + d.aportes_empleador + d.provisiones,
        cune_dian=d.cune_dian,
    )


def _prorrateo_leer(p: ProrrateoNominaObra, obra_nombre: str | None) -> ProrrateoLeer:
    return ProrrateoLeer(
        obra_id=p.obra_id, obra_nombre=obra_nombre,
        dias_imputados=p.dias_imputados, costo_imputado=p.costo_imputado,
    )


def _totales(detalles: list[DetalleLiquidacion]) -> TotalesPeriodo:
    tot = TotalesPeriodo(
        trabajadores=len(detalles), total_devengado=_CERO, total_deducciones=_CERO,
        total_neto=_CERO, total_aportes=_CERO, total_provisiones=_CERO, total_costo=_CERO,
    )
    for d in detalles:
        tot.total_devengado += d.total_devengado
        tot.total_deducciones += d.total_deducciones
        tot.total_neto += d.neto_pagar
        tot.total_aportes += d.aportes_empleador
        tot.total_provisiones += d.provisiones
        tot.total_costo += d.total_devengado + d.aportes_empleador + d.provisiones
    return tot


# --- periodos ----------------------------------------------------------------
@router.get("/periodos", response_model=list[PeriodoLeer])
async def listar_periodos(
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[PeriodoLeer]:
    """Periodos de nómina, más recientes primero."""
    return [_periodo_leer(p) for p in await service.listar_periodos()]


@router.post("/periodos", response_model=PeriodoLeer, status_code=status.HTTP_201_CREATED)
async def crear_periodo(
    payload: PeriodoCrear,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> PeriodoLeer:
    """Crea un periodo (congela el snapshot de parámetros vigente). 409 si no hay parámetros vigentes."""
    try:
        periodo = await service.crear_periodo(payload)
    except (ParametrosLegalesInexistentes, PeriodoSolapado) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _periodo_leer(periodo)


@router.get("/periodos/{periodo_id}", response_model=PeriodoDetalle)
async def obtener_periodo(
    periodo_id: int,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> PeriodoDetalle:
    """Liquidación del periodo: cabecera + snapshot + detalles por trabajador + totales. 404 si no existe."""
    try:
        periodo = await service.obtener_periodo(periodo_id)
    except PeriodoNominaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    pares = await service.detalles_con_trabajadores(periodo_id)
    detalles = [d for d, _ in pares]
    return PeriodoDetalle(
        **_periodo_leer(periodo).model_dump(),
        parametros=_snapshot(periodo),
        detalles=[_detalle_leer(d, t) for d, t in pares],
        totales=_totales(detalles),
    )


@router.get(
    "/periodos/{periodo_id}/trabajador/{trabajador_id}", response_model=TrabajadorLiquidacion
)
async def liquidacion_trabajador(
    periodo_id: int,
    trabajador_id: int,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> TrabajadorLiquidacion:
    """Detalle individual + prorrateo por obra. 404 si el periodo no existe o el trabajador no fue liquidado."""
    try:
        await service.obtener_periodo(periodo_id)
    except PeriodoNominaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    resultado = await service.liquidacion_trabajador(periodo_id, trabajador_id)
    if resultado is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"trabajador {trabajador_id} no liquidado en el periodo {periodo_id}"
        )
    detalle, trabajador, prorrateos = resultado
    return TrabajadorLiquidacion(
        detalle=_detalle_leer(detalle, trabajador),
        prorrateos=[_prorrateo_leer(p, nombre) for p, nombre in prorrateos],
    )


# --- acciones ----------------------------------------------------------------
@router.post("/periodos/{periodo_id}/liquidar", response_model=LiquidacionResultado)
async def liquidar_periodo(
    periodo_id: int,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> LiquidacionResultado:
    """Liquida a todos los trabajadores activos con actividad. Idempotente (no duplica). 409 si el
    periodo ya está cerrado; 422 si un trabajador no tiene datos para liquidar su vínculo."""
    try:
        resumen = await service.liquidar_periodo(periodo_id)
    except PeriodoNominaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PeriodoBloqueado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except TrabajadorNoLiquidable as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return LiquidacionResultado(
        periodo_id=resumen.periodo_id, estado=resumen.estado,
        trabajadores_liquidados=resumen.trabajadores_liquidados, prorrateos=resumen.prorrateos,
        total_costo=resumen.total_costo,
    )


@router.post("/periodos/{periodo_id}/cerrar", response_model=AccionResultado)
async def cerrar_periodo(
    periodo_id: int,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> AccionResultado:
    """Cierra el periodo (bloquea re-liquidación). Idempotente (reintentar = replay). 404 si no existe."""
    try:
        resumen = await service.cerrar_periodo(periodo_id)
    except PeriodoNominaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AccionResultado(periodo_id=resumen.periodo_id, estado=resumen.estado, replay=resumen.replay)


@router.post("/periodos/{periodo_id}/pagar", response_model=AccionResultado)
async def pagar_periodo(
    periodo_id: int,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("admin")),
) -> AccionResultado:
    """Marca el periodo como pagado. Idempotente (reintentar = replay). 409 si no está cerrado; 404 si no existe."""
    try:
        resumen = await service.pagar_periodo(periodo_id)
    except PeriodoNominaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PeriodoBloqueado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AccionResultado(periodo_id=resumen.periodo_id, estado=resumen.estado, replay=resumen.replay)


# --- nómina electrónica: transmisión CUNE a DIAN (Fase 7) --------------------
@router.post(
    "/periodos/{periodo_id}/transmitir-dian",
    response_model=TransmisionEncolada,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_feature("nomina_electronica"))],
)
async def transmitir_dian(
    periodo_id: int,
    service: NominaService = Depends(get_nomina_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    tenant_id: int = Depends(get_tenant_id),
    _user: Principal = Depends(require_role("admin")),
) -> TransmisionEncolada:
    """Encola la transmisión de la nómina electrónica (CUNE) del periodo a DIAN vía MATIAS.

    Gateado por la capacidad `nomina_electronica` (además del `nomina` del router): 404 sin el flag. Rol
    admin (dato sensible: salarios). Como la emisión FE, NO transmite en el request: valida y encola
    `transmitir_nomina(tenant_id, periodo_id)`; el worker transmite cada DIRECTO con reintentos/backoff.
    404 si el periodo no existe; 409 si está ABIERTO (hay que cerrarlo antes, spec 08). Idempotente: un
    detalle ya transmitido no se reprocesa, así que re-encolar no duplica el CUNE."""
    try:
        periodo = await service.obtener_periodo(periodo_id)
    except PeriodoNominaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if periodo.estado == "ABIERTO":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"periodo {periodo_id} está ABIERTO: ciérralo (LIQUIDADO) antes de transmitir a DIAN",
        )
    transmisibles = await service.contar_directos_transmitibles(periodo_id)
    await enqueuer.enqueue("transmitir_nomina", tenant_id, periodo_id)
    return TransmisionEncolada(
        periodo_id=periodo_id, estado=periodo.estado, transmisibles=transmisibles, encolado=True
    )


# --- asistencia (opcional, captura de campo) ---------------------------------
@router.post("/asistencia", response_model=AsistenciaLeer, status_code=status.HTTP_201_CREATED)
async def registrar_asistencia(
    payload: AsistenciaCrear,
    service: NominaService = Depends(get_nomina_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> AsistenciaLeer:
    """Registra un día de asistencia de un trabajador (insumo de la liquidación). Rol vendedor."""
    registro = await service.registrar_asistencia(payload)
    return AsistenciaLeer.model_validate(registro, from_attributes=True)
