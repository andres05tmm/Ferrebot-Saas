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
# Semáforo de rentabilidad (espeja `services.calculations.obra.Semaforo.value`, en minúscula).
SemaforoObra = Literal["verde", "amarillo", "rojo"]


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


# --- Fase 3: gasto real, consumo de inventario y liquidación (el diferenciador del producto) --------


class GastoRealObra(BaseModel):
    """Gasto real de una obra en tiempo real: presupuesto vs. real + semáforo + alerta de margen.

    `ingreso_presupuestado`/`utilidad_presupuestada` salen de la cotización GANADA ligada a la obra
    (subtotal+A+I+U y la U, sin el IVA que no es ingreso). `tiene_presupuesto=False` cuando la obra no
    tiene cotización (obra suelta): sin presupuesto no hay contra qué medir y el semáforo cae a `rojo`.
    `alerta_margen` avisa cuando el margen restante (`utilidad_real`) baja del 50% de la utilidad
    presupuestada — la alarma temprana antes de la pérdida (plan §4).
    """

    obra_id: int
    ingreso_presupuestado: Decimal
    utilidad_presupuestada: Decimal
    tiene_presupuesto: bool
    total_gastos: Decimal
    total_compras: Decimal
    total_prorrateo_nomina: Decimal
    total_horas_maquina: Decimal
    total_consumos_inventario: Decimal
    gasto_total: Decimal
    utilidad_real: Decimal
    semaforo: SemaforoObra
    alerta_margen: bool


class ConsumoInventarioCrear(BaseModel):
    """Alta de un consumo de material de una obra. Genera SIEMPRE el movimiento de inventario (salida).

    `costo_unitario` es opcional: si no viene, se toma del costo del producto (promedio ponderado, y si
    no, su precio de compra). `fecha` por defecto es hoy en hora Colombia (se resuelve en el servicio).
    """

    producto_id: int
    cantidad: Decimal = Field(gt=0)
    costo_unitario: Decimal | None = Field(default=None, ge=0)
    fecha: date | None = None
    responsable: str | None = None
    observaciones: str | None = None
    # M2 (tenant 0049): idempotencia del consumo cuando lo escribe el bot (reintento → replay, un solo
    # consumo + un solo movimiento de inventario). None en el alta de dashboard (comportamiento actual).
    idempotency_key: str | None = None


class ConsumoInventarioLeer(BaseModel):
    """Vista de salida de un consumo de inventario imputado a la obra."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    producto_id: int
    obra_id: int
    fecha: date
    cantidad: Decimal
    costo_unitario: Decimal
    responsable: str | None
    observaciones: str | None
    creado_en: datetime


class ConsumoInventarioRegistrado(ConsumoInventarioLeer):
    """Consumo recién registrado + traza del movimiento de inventario que generó (invariante).

    `movimiento_id` y `stock_resultante` confirman que la salida de stock quedó asentada en la misma
    transacción (nada mueve inventario sin movimiento).
    """

    movimiento_id: int | None
    stock_resultante: Decimal


class FacturaObraLeer(BaseModel):
    """Resultado de facturar una obra (Fase 7 DIAN): el documento FE ligado a la obra + si nació ahora.

    `factura_id`/`estado`/`cufe`/`prefijo`/`consecutivo` describen el documento electrónico (arranca
    `pendiente` sin CUFE; el worker lo lleva a `aceptada` con su CUFE al emitir contra MATIAS). `creada`
    distingue el documento NUEVO (se encoló la emisión) del ya existente (idempotencia: no se emite un
    segundo CUFE). `venta_id` es la venta INTERNA que respalda la factura (reuso del pipeline venta→FE)."""

    obra_id: int
    factura_id: int
    venta_id: int | None
    tipo: str
    estado: str
    prefijo: str | None
    consecutivo: int | None
    cufe: str | None
    creada: bool


class LiquidacionObraLeer(BaseModel):
    """Vista de salida del snapshot inmutable de la liquidación de una obra."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    obra_id: int
    fecha_liquidacion: datetime
    ingreso_presupuestado: Decimal
    utilidad_presupuestada: Decimal
    gasto_total: Decimal
    total_gastos: Decimal
    total_compras: Decimal
    total_prorrateo_nomina: Decimal
    total_horas_maquina: Decimal
    total_consumos_inventario: Decimal
    utilidad_real: Decimal
    semaforo: SemaforoObra
    snapshot_json: dict
    creado_en: datetime
