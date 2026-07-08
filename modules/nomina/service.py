"""Servicio de nómina: orquesta el motor puro sobre la asistencia y persiste el resultado.

Toda la aritmética de dinero vive en `services.calculations.nomina` (una fórmula, una verdad, skill
money-safe): este servicio arma el snapshot `ParametrosNomina` desde el periodo, agrega la asistencia,
llama `liquidar_directo`/`liquidar_patacaliente` y `prorratear_nomina_obra`, y guarda por el repo. No
recalcula dinero.

Ciclo de vida del periodo (estado): ABIERTO → LIQUIDADO (cerrado) → PAGADO.
  - `liquidar_periodo` solo corre sobre ABIERTO y es IDEMPOTENTE: re-liquidar recomputa sobre la misma
    asistencia y, vía UPSERT del detalle + reemplazo de prorrateos, deja exactamente un juego de filas
    (invariante test-primero de idempotencia).
  - `cerrar_periodo`/`pagar_periodo` son idempotentes sobre su estado destino (reintentar = replay).

Invariante de conciliación (test-primero): Σ `costo_imputado` del periodo ≡ Σ costo total liquidado
(total_devengado + aportes + provisiones) por trabajador. Lo garantiza la función pura de reparto por
mayor resto; aquí se preserva persistiendo fielmente lo que devuelve.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from core.config.timezone import now_co, today_co
from core.logging import get_logger
from core.money import cuantizar
from modules.nomina.errors import (
    ParametrosLegalesInexistentes,
    PeriodoBloqueado,
    PeriodoNominaInexistente,
    TrabajadorNoLiquidable,
)
from modules.nomina.models import (
    DetalleLiquidacion,
    ParametrosLegales,
    PeriodoNomina,
    ProrrateoNominaObra,
)
from modules.nomina.schemas import AsistenciaCrear, PeriodoCrear
from modules.trabajadores.models import RegistroAsistencia, Trabajador
from services.calculations.nomina import (
    Liquidacion,
    ParametrosNomina,
    ProrrateoObra,
    liquidar_directo,
    liquidar_patacaliente,
    prorratear_nomina_obra,
)

log = get_logger("nomina.service")

CERO = Decimal("0")

# Ausencias NO remuneradas: sus días NO cuentan como trabajados (spec 08: "days ... no unpaid absence").
# Las demás (INCAPACIDAD/LICENCIA_REMUNERADA/VACACIONES) sí, [DEFINIR contador] el trato fino de cada una.
_AUSENCIAS_NO_REMUNERADAS = frozenset({"LICENCIA_NO_REMUNERADA", "FALTA_INJUSTIFICADA"})


# --- inputs duck-typed que espera el motor puro (Protocol de services.calculations.nomina) ----------
@dataclass(frozen=True, slots=True)
class _TrabDirecto:
    salario_base: Decimal


@dataclass(frozen=True, slots=True)
class _Asistencia:
    dias_trabajados: Decimal
    horas_extra_diurnas: Decimal
    horas_extra_nocturnas: Decimal
    horas_dominicales: Decimal


@dataclass(slots=True)
class _AgregadoAsistencia:
    """Asistencia de un trabajador agregada en el periodo (insumo de la liquidación)."""

    dias_trabajados: Decimal = CERO
    horas_trabajadas: Decimal = CERO   # para patacaliente (horas × tarifa)
    horas_extra_diurnas: Decimal = CERO
    horas_extra_nocturnas: Decimal = CERO
    horas_dominicales: Decimal = CERO
    # Días trabajados por obra (clave str o None=administrativo) → pesos del prorrateo. Suma = dias_trabajados.
    dias_por_obra: dict[str | None, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResumenLiquidacion:
    periodo_id: int
    estado: str
    trabajadores_liquidados: int
    prorrateos: int
    total_costo: Decimal


@dataclass(frozen=True, slots=True)
class ResumenAccion:
    periodo_id: int
    estado: str
    replay: bool


def _agregar_asistencia(registros: list[RegistroAsistencia]) -> _AgregadoAsistencia:
    """Agrega los registros de un trabajador: cuenta días trabajados (sin ausencia no remunerada), suma
    horas y horas extra, y arma `dias_por_obra` con los MISMOS días trabajados (para que el prorrateo
    concilie contra el costo total)."""
    agg = _AgregadoAsistencia()
    for r in registros:
        if r.ausencia in _AUSENCIAS_NO_REMUNERADAS:
            continue   # día no remunerado: no cuenta ni para salario ni para prorrateo
        agg.dias_trabajados += Decimal(1)
        agg.horas_trabajadas += r.horas_trabajadas
        agg.horas_extra_diurnas += r.horas_extra_diurnas
        agg.horas_extra_nocturnas += r.horas_extra_nocturnas
        agg.horas_dominicales += r.horas_dominical_festivo   # el ORM lo llama _festivo; el motor _dominicales
        clave = str(r.obra_id) if r.obra_id is not None else None
        agg.dias_por_obra[clave] = agg.dias_por_obra.get(clave, CERO) + Decimal(1)
    return agg


def _snapshot_a_parametros(periodo: PeriodoNomina) -> ParametrosNomina:
    """Mapea el snapshot congelado del periodo (`param_*`) al dataclass que consume el motor."""
    return ParametrosNomina(
        smmlv=periodo.param_smmlv,
        auxilio_transporte=periodo.param_auxilio_transporte,
        auxilio_transporte_tope_smmlv=periodo.param_auxilio_transporte_tope_smmlv,
        horas_mes=periodo.param_horas_mes,
        recargo_he_diurna=periodo.param_recargo_he_diurna,
        recargo_he_nocturna=periodo.param_recargo_he_nocturna,
        recargo_dominical=periodo.param_recargo_dominical,
        salud_empleado_pct=periodo.param_salud_empleado_pct,
        pension_empleado_pct=periodo.param_pension_empleado_pct,
        salud_empleador_pct=periodo.param_salud_empleador_pct,
        pension_empleador_pct=periodo.param_pension_empleador_pct,
        arl_pct=periodo.param_arl_pct,
        caja_compensacion_pct=periodo.param_caja_compensacion_pct,
        sena_pct=periodo.param_sena_pct,
        icbf_pct=periodo.param_icbf_pct,
        cesantias_pct=periodo.param_cesantias_pct,
        intereses_cesantias_pct=periodo.param_intereses_cesantias_pct,
        prima_pct=periodo.param_prima_pct,
        vacaciones_pct=periodo.param_vacaciones_pct,
    )


class NominaRepo(Protocol):
    """Puerto de datos de nómina (lo implementa `SqlNominaRepository`; los tests lo pueden falsear)."""

    async def parametros_vigentes(self, fecha: date) -> ParametrosLegales | None: ...
    async def crear_periodo(self, datos: PeriodoCrear, params: ParametrosLegales) -> PeriodoNomina: ...
    async def obtener_periodo(self, periodo_id: int) -> PeriodoNomina | None: ...
    async def listar_periodos(self) -> list[PeriodoNomina]: ...
    async def marcar_estado(
        self, periodo: PeriodoNomina, estado: str, *, ahora: datetime,
        liquidado_en: datetime | None = ..., pagado_en: datetime | None = ...,
    ) -> None: ...
    async def trabajadores_activos(self) -> list[Trabajador]: ...
    async def asistencia_de(
        self, trabajador_id: int, desde: date, hasta: date
    ) -> list[RegistroAsistencia]: ...
    async def registrar_asistencia(self, datos: AsistenciaCrear, fecha: date) -> RegistroAsistencia: ...
    async def upsert_detalle(
        self, periodo_id: int, trabajador_id: int, *, tipo_vinculacion: str, dias: Decimal,
        liq: Liquidacion, ahora: datetime,
    ) -> DetalleLiquidacion: ...
    async def reemplazar_prorrateos(
        self, periodo_id: int, trabajador_id: int, prorrateos: list[ProrrateoObra], *, ahora: datetime,
    ) -> int: ...
    async def eliminar_liquidaciones_ausentes(
        self, periodo_id: int, trabajador_ids_liquidados: list[int]
    ) -> int: ...
    async def detalles_de(self, periodo_id: int) -> list[DetalleLiquidacion]: ...
    async def detalle_de(self, periodo_id: int, trabajador_id: int) -> DetalleLiquidacion | None: ...
    async def prorrateos_de_trabajador(
        self, periodo_id: int, trabajador_id: int
    ) -> list[ProrrateoNominaObra]: ...
    async def trabajadores_map(self, ids: list[int]) -> dict[int, Trabajador]: ...
    async def obras_nombres(self, ids: list[int]) -> dict[int, str]: ...
    async def contar_transmitibles(self, periodo_id: int) -> int: ...


class NominaService:
    def __init__(self, repo: NominaRepo) -> None:
        self._repo = repo

    # --- periodos -------------------------------------------------------------
    async def crear_periodo(self, datos: PeriodoCrear) -> PeriodoNomina:
        """Crea un periodo congelando el snapshot de `parametros_legales` vigente al inicio del periodo.

        Sin fila vigente → `ParametrosLegalesInexistentes` (409): el motor jamás inventa valores legales.
        """
        params = await self._repo.parametros_vigentes(datos.fecha_inicio)
        if params is None:
            raise ParametrosLegalesInexistentes(
                f"no hay parámetros legales vigentes al {datos.fecha_inicio.isoformat()}"
            )
        periodo = await self._repo.crear_periodo(datos, params)
        log.info(
            "periodo_nomina_creado", periodo_id=periodo.id, tipo=periodo.tipo,
            desde=str(periodo.fecha_inicio), hasta=str(periodo.fecha_fin),
        )
        return periodo

    async def obtener_periodo(self, periodo_id: int) -> PeriodoNomina:
        periodo = await self._repo.obtener_periodo(periodo_id)
        if periodo is None:
            raise PeriodoNominaInexistente(periodo_id)
        return periodo

    async def listar_periodos(self) -> list[PeriodoNomina]:
        return await self._repo.listar_periodos()

    # --- liquidación ----------------------------------------------------------
    async def liquidar_periodo(self, periodo_id: int) -> ResumenLiquidacion:
        """Liquida a cada trabajador activo con actividad en el periodo y persiste detalle + prorrateo.

        Idempotente: solo corre sobre un periodo ABIERTO; el UPSERT del detalle y el reemplazo de
        prorrateos garantizan que re-liquidar no duplique filas.
        """
        periodo = await self.obtener_periodo(periodo_id)
        if periodo.estado != "ABIERTO":
            raise PeriodoBloqueado(
                f"periodo {periodo_id} está {periodo.estado}: no se puede re-liquidar (reábrelo no es "
                "posible en v1)"
            )

        params = _snapshot_a_parametros(periodo)
        ahora = now_co()
        trabajadores = await self._repo.trabajadores_activos()

        n_detalles = 0
        n_prorrateos = 0
        total_costo = CERO
        liquidados: list[int] = []
        for t in trabajadores:
            registros = await self._repo.asistencia_de(
                t.id, periodo.fecha_inicio, periodo.fecha_fin
            )
            agg = _agregar_asistencia(registros)
            if agg.dias_trabajados <= 0 and agg.horas_trabajadas <= 0:
                continue   # sin actividad en el periodo: no se liquida

            liq, dias = self._liquidar_trabajador(t, agg, params)
            await self._repo.upsert_detalle(
                periodo.id, t.id, tipo_vinculacion=t.tipo_vinculacion, dias=dias, liq=liq, ahora=ahora
            )
            prorrateos = prorratear_nomina_obra(liq, agg.dias_por_obra)
            n = await self._repo.reemplazar_prorrateos(periodo.id, t.id, prorrateos, ahora=ahora)

            liquidados.append(t.id)
            n_detalles += 1
            n_prorrateos += n
            total_costo += liq.total_devengado + liq.aportes_empleador + liq.provisiones

        # Reemplazo ATÓMICO del set liquidado (idempotencia, MEDIUM-1): purga detalle+prorrateo de
        # quien YA NO liquida (0 días o dado de baja) para que sus filas viejas no inflen los totales
        # del periodo ni el costo de obra al re-liquidar.
        await self._repo.eliminar_liquidaciones_ausentes(periodo.id, liquidados)

        log.info(
            "periodo_nomina_liquidado", periodo_id=periodo.id,
            trabajadores=n_detalles, prorrateos=n_prorrateos, total_costo=str(cuantizar(total_costo)),
        )
        return ResumenLiquidacion(
            periodo_id=periodo.id, estado=periodo.estado,
            trabajadores_liquidados=n_detalles, prorrateos=n_prorrateos,
            total_costo=cuantizar(total_costo),
        )

    def _liquidar_trabajador(
        self, t: Trabajador, agg: _AgregadoAsistencia, params: ParametrosNomina
    ) -> tuple[Liquidacion, Decimal]:
        """Elige el motor por `tipo_vinculacion` y devuelve (liquidación, días liquidados)."""
        if t.tipo_vinculacion == "DIRECTO":
            if t.salario_base is None:
                raise TrabajadorNoLiquidable(t.id, "DIRECTO sin salario_base")
            # Honra el flag `aplica_aux_transporte` del trabajador (además del tope legal por salario que ya
            # aplica el motor): si NO aplica, se liquida contra un snapshot con auxilio_transporte=0 → el
            # motor lo deriva a 0 (y de ahí sus provisiones prestacionales), sin duplicar la fórmula del
            # motor puro (`services.calculations.nomina`, que a propósito no conoce el flag del trabajador).
            params_trab = params if t.aplica_aux_transporte else replace(params, auxilio_transporte=CERO)
            liq = liquidar_directo(
                _TrabDirecto(salario_base=t.salario_base),
                _Asistencia(
                    dias_trabajados=agg.dias_trabajados,
                    horas_extra_diurnas=agg.horas_extra_diurnas,
                    horas_extra_nocturnas=agg.horas_extra_nocturnas,
                    horas_dominicales=agg.horas_dominicales,
                ),
                params_trab,
            )
            return liq, agg.dias_trabajados
        # PATACALIENTE: por hora, sin deducciones/aportes/provisiones (spec 08).
        if t.tarifa_hora is None:
            raise TrabajadorNoLiquidable(t.id, "PATACALIENTE sin tarifa_hora")
        liq = liquidar_patacaliente(agg.horas_trabajadas, t.tarifa_hora)
        return liq, agg.dias_trabajados

    # --- cierre / pago (idempotentes) -----------------------------------------
    async def cerrar_periodo(self, periodo_id: int) -> ResumenAccion:
        """Cierra el periodo (bloquea re-liquidar). Idempotente: ya cerrado/pagado → replay."""
        periodo = await self.obtener_periodo(periodo_id)
        if periodo.estado in ("LIQUIDADO", "PAGADO"):
            return ResumenAccion(periodo_id=periodo.id, estado=periodo.estado, replay=True)
        ahora = now_co()
        await self._repo.marcar_estado(periodo, "LIQUIDADO", ahora=ahora, liquidado_en=ahora)
        log.info("periodo_nomina_cerrado", periodo_id=periodo.id)
        return ResumenAccion(periodo_id=periodo.id, estado="LIQUIDADO", replay=False)

    async def pagar_periodo(self, periodo_id: int) -> ResumenAccion:
        """Marca el periodo como PAGADO. Idempotente: ya pagado → replay; abierto → 409 (cierra antes)."""
        periodo = await self.obtener_periodo(periodo_id)
        if periodo.estado == "PAGADO":
            return ResumenAccion(periodo_id=periodo.id, estado="PAGADO", replay=True)
        if periodo.estado != "LIQUIDADO":
            raise PeriodoBloqueado(
                f"periodo {periodo_id} está {periodo.estado}: ciérralo (LIQUIDADO) antes de pagar"
            )
        ahora = now_co()
        await self._repo.marcar_estado(periodo, "PAGADO", ahora=ahora, pagado_en=ahora)
        log.info("periodo_nomina_pagado", periodo_id=periodo.id)
        return ResumenAccion(periodo_id=periodo.id, estado="PAGADO", replay=False)

    # --- lecturas para el router (resuelven nombres sin N+1) ------------------
    async def detalles_con_trabajadores(
        self, periodo_id: int
    ) -> list[tuple[DetalleLiquidacion, Trabajador | None]]:
        """Detalles del periodo + el trabajador de cada uno (para nombre/documento en la vista)."""
        detalles = await self._repo.detalles_de(periodo_id)
        tmap = await self._repo.trabajadores_map([d.trabajador_id for d in detalles])
        return [(d, tmap.get(d.trabajador_id)) for d in detalles]

    async def liquidacion_trabajador(
        self, periodo_id: int, trabajador_id: int
    ) -> tuple[DetalleLiquidacion, Trabajador | None, list[tuple[ProrrateoNominaObra, str | None]]] | None:
        """Detalle individual + prorrateo por obra (con nombre de obra). None si el trabajador no tiene
        detalle en el periodo (→ 404 en el router)."""
        detalle = await self._repo.detalle_de(periodo_id, trabajador_id)
        if detalle is None:
            return None
        tmap = await self._repo.trabajadores_map([trabajador_id])
        prorrateos = await self._repo.prorrateos_de_trabajador(periodo_id, trabajador_id)
        obra_ids = [p.obra_id for p in prorrateos if p.obra_id is not None]
        nombres = await self._repo.obras_nombres(obra_ids)
        enriquecidos = [(p, nombres.get(p.obra_id) if p.obra_id is not None else None) for p in prorrateos]
        return detalle, tmap.get(trabajador_id), enriquecidos

    # --- nómina electrónica (ack del endpoint de transmisión) -----------------
    async def contar_directos_transmitibles(self, periodo_id: int) -> int:
        """Cuántos trabajadores DIRECTO del periodo faltan por transmitir a DIAN (PENDIENTE/ERROR).

        Solo lectura para el ACK del endpoint `/transmitir-dian`: la transmisión real corre en el worker
        (job `transmitir_nomina`). El patacaliente no cuenta (no lleva CUNE, spec 08)."""
        return await self._repo.contar_transmitibles(periodo_id)

    # --- asistencia (endpoint opcional) ---------------------------------------
    async def registrar_asistencia(self, datos: AsistenciaCrear) -> RegistroAsistencia:
        """Registra un día de asistencia. `fecha` por defecto hoy Colombia (regla #4)."""
        fecha = datos.fecha or today_co()
        return await self._repo.registrar_asistencia(datos, fecha)
