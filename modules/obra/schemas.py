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
    """Vista de salida de una obra. `cotizacion_id` es de solo lectura (lo poblará la Fase 2).

    `cliente_nombre` es azúcar de lectura (no es columna de `obras`): el router lo resuelve en lote por
    `SqlObrasRepository.nombres_clientes` y lo inyecta. Default `None` para que `model_validate` sobre el
    ORM (que no tiene el atributo) no falle; el listado lo rellena antes de responder."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cotizacion_id: int | None
    cliente_id: int
    cliente_nombre: str | None = None
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


class ObraPanelItem(BaseModel):
    """Una obra en el panel/home (Fase 8): su mini-resumen financiero + semáforo + alerta de margen.

    `cliente_nombre` (Fase cockpit) lo resuelve `panel()` en lote (sin N+1); `None` si el cliente no existe."""

    obra_id: int
    nombre: str
    estado: str
    cliente_id: int
    cliente_nombre: str | None = None
    ingreso_presupuestado: Decimal
    gasto_total: Decimal
    utilidad_real: Decimal
    tiene_presupuesto: bool
    semaforo: SemaforoObra
    alerta_margen: bool


class ObraPanel(BaseModel):
    """Home de obra (Fase 8): overview del portafolio (conteo por estado + rollup financiero + alertas) +
    una fila por obra viva. Endpoint agregado y cacheado (GET /obras/panel): vista de solo lectura del
    portafolio para arrancar el dashboard sin abrir cada obra."""

    generado_en: datetime
    total_obras: int
    obras_activas: int
    por_estado: dict[str, int]
    ingreso_presupuestado_total: Decimal
    gasto_total: Decimal
    utilidad_real_total: Decimal
    obras_en_alerta: int
    obras: list[ObraPanelItem]


# --- Cockpit de construcción (GET /obras/dashboard): endpoint agregado del vertical (spec 13) --------
# Todo el dinero es Decimal (serializa como string). Ventanas de mes calendario en hora Colombia.

# Semáforo de la utilidad del mes: rojo si hay pérdida (<0), amarillo si el margen es 0–3%, verde si ≥3%
# (márgenes de obra civil de 3–4%: por debajo del 3% ya hay que mirar de cerca).
SemaforoUtilidad = Literal["verde", "amarillo", "rojo"]
# Severidad de una alerta accionable (se ordenan rojo→amarillo).
SeveridadAlerta = Literal["rojo", "amarillo"]


class MesRango(BaseModel):
    """Ventana del mes calendario (hora Colombia) sobre la que se calculan los KPIs."""

    desde: date
    hasta: date


class KpisMesAnterior(BaseModel):
    """Comparativo del mes anterior (para el Δ% de los tiles del cockpit)."""

    ingreso_total: Decimal
    gasto_total: Decimal


class KpisMes(BaseModel):
    """KPIs financieros del mes en curso.

    `ingreso_total = ingreso_alquiler + resbalos`; `gasto_total = gastos + compras`;
    `utilidad_estimada = ingreso_total − gasto_total`; `margen_pct = utilidad_estimada / ingreso_total × 100`
    (0 si no hubo ingreso). `flujo_caja_neto` en v1 iguala `utilidad_estimada` (no hay un ledger de caja
    consolidado del mes que lo separe todavía — DOCUMENTADO). Nómina NO entra en `gasto_total` del mes en
    v1 (el KPI se descompone en `gastos`+`compras`); el costo de nómina se refleja por obra en el portafolio."""

    ingreso_alquiler: Decimal
    resbalos: Decimal
    ingreso_total: Decimal
    gastos: Decimal
    compras: Decimal
    gasto_total: Decimal
    utilidad_estimada: Decimal
    margen_pct: Decimal
    semaforo_utilidad: SemaforoUtilidad
    flujo_caja_neto: Decimal
    mes_anterior: KpisMesAnterior


class MaquinaOcupadaHoy(BaseModel):
    """Una máquina OCUPADA hoy: obra/operador donde está y horas/ingreso del día (0 si aún sin parte)."""

    maquina_id: int
    maquina: str
    obra_nombre: str | None
    operador_nombre: str | None
    horas_hoy: Decimal
    ingreso_hoy: Decimal


class TopMaquinaMes(BaseModel):
    """Una máquina del top del mes por horas facturadas (+ el ingreso que generó)."""

    maquina_id: int
    maquina: str
    horas: Decimal
    ingreso: Decimal


class MaquinasDashboard(BaseModel):
    """Tablero de máquinas: conteo total, conteo por estado, ocupadas hoy y top del mes."""

    total: int
    por_estado: dict[str, int]
    ocupadas_hoy: list[MaquinaOcupadaHoy]
    top_mes: list[TopMaquinaMes]


class AlertaDashboard(BaseModel):
    """Alerta accionable del cockpit. `ruta` es el destino en el dashboard (para el click).

    `tipo`: `mantenimiento_vencido` | `mantenimiento_proximo` | `obra_perdida` | `obra_margen`. `ref_id`
    identifica la entidad (máquina u obra) para resaltarla al aterrizar. Se listan rojo→amarillo."""

    tipo: str
    severidad: SeveridadAlerta
    titulo: str
    detalle: str
    ref_id: int | None
    ruta: str


class ConteosDashboard(BaseModel):
    """Badges numéricos del cockpit (pendientes vivos, no acotados al mes)."""

    gastos_por_revisar: int
    colitas: int
    cotizaciones_por_vencer: int


class DashboardConstruccion(BaseModel):
    """Respuesta agregada del cockpit del vertical construcción (GET /obras/dashboard).

    Un solo request que arma la portada "todo de un vistazo": KPIs del mes con semáforo y comparativo,
    portafolio de obras (reusa el panel + `cliente_nombre`), tablero de máquinas, alertas accionables y
    badges. Admin-only; secciones opcionales (colitas, cotizaciones) degradan por capacidad. Cacheado 5 min."""

    generado_en: datetime
    mes: MesRango
    kpis_mes: KpisMes
    portafolio: ObraPanel
    maquinas: MaquinasDashboard
    alertas: list[AlertaDashboard]
    conteos: ConteosDashboard


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
