"""Contratos Pydantic de obras y sus reportes diarios (vertical construcción, spec cliente 04 / tenant
0044).

Los campos calcan las columnas del ORM (`modules.obra.models`) con sus nombres en español TAL CUAL.

FASE 2 (no implementada aquí): la conversión de una cotización GANADA en Obra. Por eso `cotizacion_id`
NO está en `ObraCrear` (una obra de Fase 1 se crea "suelta", sin cotización) y en `ObraLeer` es de
SOLO LECTURA: cuando exista el flujo de conversión, esa FK la poblará el service de Fase 2, no el CRUD.

El `estado` tampoco se fija al crear (arranca en PLANIFICADA por defecto de la base) ni se edita por el
PATCH de metadatos: las transiciones van por el endpoint dedicado `PATCH /obras/{id}/estado`, que valida
el ciclo de vida (nada de estados imposibles).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EstadoObra = Literal[
    "PLANIFICADA", "EN_EJECUCION", "SUSPENDIDA", "FINALIZADA", "LIQUIDADA"
]
OrigenRegistro = Literal["MANUAL", "TELEGRAM_BOT", "IMPORTACION"]


class ObraCrear(BaseModel):
    """Alta de una obra suelta (sin cotización; la conversión desde cotización es Fase 2)."""

    cliente_id: int
    nombre: str = Field(min_length=1)
    ubicacion: str | None = None
    fecha_inicio: date | None = None
    fecha_fin_estimada: date | None = None
    fecha_fin_real: date | None = None
    notas: str | None = None


class ObraActualizar(BaseModel):
    """Edición parcial de metadatos (PATCH). NO cambia `estado` (eso va por el endpoint de transición)."""

    cliente_id: int | None = None
    nombre: str | None = Field(default=None, min_length=1)
    ubicacion: str | None = None
    fecha_inicio: date | None = None
    fecha_fin_estimada: date | None = None
    fecha_fin_real: date | None = None
    notas: str | None = None


class ObraEstadoCambiar(BaseModel):
    """Solicitud de transición de estado (el servicio valida que sea permitida)."""

    estado: EstadoObra


class ObraLeer(BaseModel):
    """Vista de salida de una obra. `cotizacion_id` es de solo lectura (lo poblará la Fase 2)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cotizacion_id: int | None
    cliente_id: int
    nombre: str
    ubicacion: str | None
    fecha_inicio: date | None
    fecha_fin_estimada: date | None
    fecha_fin_real: date | None
    estado: str
    notas: str | None
    creado_en: datetime
    actualizado_en: datetime


class ObraResumen(ObraLeer):
    """Detalle de una obra con conteos baratos de su operación (GET /obras/{id}).

    Los agregados pesados (presupuesto vs. gasto real) son de la Fase 3; aquí solo se cuentan las filas
    asociadas por sus índices `obra_id` (tres COUNT baratos), útil como panorama del detalle.
    """

    maquinas_asignadas: int
    trabajadores_asignados: int
    reportes_diarios: int


class ReporteDiarioCrear(BaseModel):
    """Alta de un reporte diario de avance. Por defecto `origen_registro=MANUAL` (alta desde dashboard).

    El bot de Telegram tiene su propio camino (origen TELEGRAM_BOT); esta ruta HTTP es la carga manual.
    """

    fecha: date | None = None   # default hoy Colombia en el servicio
    reportado_por: str | None = None
    telegram_user_id: str | None = None
    avance_descripcion: str | None = None
    m2_ejecutados: Decimal | None = Field(default=None, ge=0)
    m3_ejecutados: Decimal | None = Field(default=None, ge=0)
    incidentes: str | None = None
    foto_urls: list[str] = Field(default_factory=list)
    origen_registro: OrigenRegistro = "MANUAL"


class ReporteDiarioLeer(BaseModel):
    """Vista de salida de un reporte diario de avance."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    obra_id: int
    fecha: date
    reportado_por: str | None
    telegram_user_id: str | None
    avance_descripcion: str | None
    m2_ejecutados: Decimal | None
    m3_ejecutados: Decimal | None
    incidentes: str | None
    foto_urls: list[str]
    origen_registro: str
    creado_en: datetime
