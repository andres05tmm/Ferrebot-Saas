"""Contratos Pydantic de nómina (Fase 4 PIM, spec cliente 08).

Los montos viajan como `Decimal` (el motor `services.calculations.nomina` los entrega ya cuantizados);
el router serializa. Las lecturas que cruzan tablas (nombre del trabajador, nombre de la obra) se
ARMAN en el router (no vía `from_attributes`), igual que `modules.contabilidad` con las cuentas: el
repo devuelve los ORM + los mapas de nombres y el router compone el DTO.

`ParametrosSnapshot` refleja el snapshot congelado del periodo (columnas `param_*`) para que el contador
pueda verificar con qué valores se liquidó (invariante de la spec: los parámetros se congelan al crear).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TipoPeriodo = Literal["QUINCENAL", "MENSUAL", "SEMANAL"]
EstadoPeriodo = Literal["ABIERTO", "LIQUIDADO", "PAGADO"]
TipoVinculacion = Literal["DIRECTO", "PATACALIENTE"]


class PeriodoCrear(BaseModel):
    """Alta de un periodo de nómina. Congela el snapshot de `parametros_legales` vigente (en el service)."""

    tipo: TipoPeriodo = "QUINCENAL"
    fecha_inicio: date
    fecha_fin: date
    nombre: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _rango_valido(self) -> "PeriodoCrear":
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")
        return self


class ParametrosSnapshot(BaseModel):
    """Valores legales congelados con los que se liquida el periodo (columnas `param_*`)."""

    smmlv: Decimal
    auxilio_transporte: Decimal
    auxilio_transporte_tope_smmlv: int
    horas_mes: Decimal
    recargo_he_diurna: Decimal
    recargo_he_nocturna: Decimal
    recargo_dominical: Decimal
    salud_empleado_pct: Decimal
    pension_empleado_pct: Decimal
    salud_empleador_pct: Decimal
    pension_empleador_pct: Decimal
    arl_pct: Decimal
    caja_compensacion_pct: Decimal
    sena_pct: Decimal
    icbf_pct: Decimal
    cesantias_pct: Decimal
    intereses_cesantias_pct: Decimal
    prima_pct: Decimal
    vacaciones_pct: Decimal


class PeriodoLeer(BaseModel):
    """Vista de lista de un periodo (sin los detalles)."""

    id: int
    nombre: str | None
    tipo: str
    fecha_inicio: date
    fecha_fin: date
    estado: str
    liquidado_en: datetime | None
    pagado_en: datetime | None
    creado_en: datetime


class DetalleLiquidacionLeer(BaseModel):
    """Liquidación de un trabajador (con su nombre/documento resueltos en el router)."""

    id: int
    trabajador_id: int
    trabajador_nombre: str
    trabajador_documento: str
    tipo_vinculacion: str
    dias_liquidados: Decimal
    salario_devengado: Decimal
    auxilio_transporte: Decimal
    valor_horas_extra: Decimal
    total_devengado: Decimal
    salud_empleado: Decimal
    pension_empleado: Decimal
    total_deducciones: Decimal
    neto_pagar: Decimal
    aportes_empleador: Decimal
    provisiones: Decimal
    costo_total: Decimal   # total_devengado + aportes_empleador + provisiones (lo que cuesta la obra)
    cune_dian: str | None


class ProrrateoLeer(BaseModel):
    """Una porción del costo de un trabajador imputada a una obra (o a administración)."""

    obra_id: int | None
    obra_nombre: str | None   # None (obra_id NULL) = administrativo
    dias_imputados: Decimal
    costo_imputado: Decimal


class TotalesPeriodo(BaseModel):
    """Totales agregados del periodo (para la cabecera de la liquidación)."""

    trabajadores: int
    total_devengado: Decimal
    total_deducciones: Decimal
    total_neto: Decimal
    total_aportes: Decimal
    total_provisiones: Decimal
    total_costo: Decimal


class PeriodoDetalle(PeriodoLeer):
    """Liquidación del periodo: cabecera + snapshot + detalles por trabajador + totales."""

    parametros: ParametrosSnapshot
    detalles: list[DetalleLiquidacionLeer]
    totales: TotalesPeriodo


class TrabajadorLiquidacion(BaseModel):
    """Detalle individual + prorrateo por obra (GET .../trabajador/{tid})."""

    detalle: DetalleLiquidacionLeer
    prorrateos: list[ProrrateoLeer]


class LiquidacionResultado(BaseModel):
    """Salida de POST .../liquidar: cuántos trabajadores/filas y el costo total del periodo."""

    periodo_id: int
    estado: str
    trabajadores_liquidados: int
    prorrateos: int
    total_costo: Decimal


class AccionResultado(BaseModel):
    """Salida de cerrar/pagar: estado resultante + `replay` (True si ya estaba en ese estado)."""

    periodo_id: int
    estado: str
    replay: bool


class AsistenciaCrear(BaseModel):
    """Registro de asistencia de un día (insumo de la liquidación). Endpoint opcional (rol vendedor).

    `obra_id` NULL = día administrativo (no imputable a una obra). `fecha` por defecto hoy Colombia
    (se resuelve en el service). Los literales de `ausencia` son EXACTOS a la spec.
    """

    trabajador_id: int
    fecha: date | None = None
    obra_id: int | None = None
    horas_trabajadas: Decimal = Field(default=Decimal("8"), ge=0)
    horas_extra_diurnas: Decimal = Field(default=Decimal("0"), ge=0)
    horas_extra_nocturnas: Decimal = Field(default=Decimal("0"), ge=0)
    horas_dominical_festivo: Decimal = Field(default=Decimal("0"), ge=0)
    ausencia: Literal[
        "INCAPACIDAD", "LICENCIA_REMUNERADA", "LICENCIA_NO_REMUNERADA", "VACACIONES",
        "FALTA_INJUSTIFICADA",
    ] | None = None
    observaciones: str | None = None


class AsistenciaLeer(BaseModel):
    id: int
    trabajador_id: int
    fecha: date
    obra_id: int | None
    horas_trabajadas: Decimal
    horas_extra_diurnas: Decimal
    horas_extra_nocturnas: Decimal
    horas_dominical_festivo: Decimal
    ausencia: str | None
    observaciones: str | None
