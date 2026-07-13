"""Contratos Pydantic de caja y gastos (api-contract.md §caja/gastos)."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CajaMovTipo = Literal["ingreso", "egreso"]
GastoCategoria = Literal["transporte", "papeleria", "servicios", "nomina", "mantenimiento", "otros"]

# --- Vertical construcción (spec 09). Literales EXACTOS a la spec 01_MODELO_DATOS y a los enums de la
# migración 0048. La categoría del vertical (`categoria_gasto`) convive con la `categoria` del POS de
# arriba: son dos taxonomías distintas en la misma tabla. -----------------------------------------------
CategoriaGastoVertical = Literal[
    "REPUESTOS", "MANTENIMIENTO_MAQUINA", "ALMUERZOS", "TRANSPORTE_PERSONAL", "COMBUSTIBLE",
    "PAPELERIA", "SERVICIOS_PUBLICOS", "ARRIENDO", "IMPUESTOS", "OTRO",
]
MetodoPagoGasto = Literal[
    "EFECTIVO", "TRANSFERENCIA_BANCOLOMBIA", "TRANSFERENCIA_OTRO_BANCO", "TARJETA_CREDITO",
    "TARJETA_DEBITO", "CHEQUE",
]
OrigenRegistro = Literal["MANUAL", "TELEGRAM_BOT", "IMPORTACION"]


class AperturaCrear(BaseModel):
    saldo_inicial: Decimal = Field(ge=0)


class CierreCrear(BaseModel):
    saldo_contado: Decimal = Field(ge=0)


class MovimientoCrear(BaseModel):
    tipo: CajaMovTipo
    monto: Decimal = Field(gt=0)
    concepto: str | None = None


class GastoCrear(BaseModel):
    categoria: GastoCategoria
    monto: Decimal = Field(gt=0)
    concepto: str | None = None
    # Vínculo opcional a cuentas por pagar (ADR 0028): a quién se le pagó y qué factura salda este
    # gasto. Con `factura_proveedor_id`, el gasto genera SU único abono (no se registra otro aparte).
    proveedor_id: int | None = None
    factura_proveedor_id: str | None = None
    # --- Vertical construcción (spec 09). Todo OPCIONAL: el POS retail no lo usa. ---
    # `obra_id`/`maquina_id` imputan el gasto a una obra/máquina (sigue siendo gasto de caja normal). El
    # bot rellena `origen_registro`/`telegram_*` y marca `requiere_revision` cuando la extracción tiene
    # baja confianza. `origen_registro`/`requiere_revision` en None → aplican los server_default (0048).
    obra_id: int | None = None
    maquina_id: int | None = None
    categoria_gasto: CategoriaGastoVertical | None = None
    metodo_pago: MetodoPagoGasto | None = None
    numero_referencia: str | None = None
    comprobante_url: str | None = None
    origen_registro: OrigenRegistro | None = None
    telegram_user_id: str | None = None
    telegram_message_id: str | None = None
    requiere_revision: bool | None = None


class CajaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int | None
    fecha_apertura: datetime
    saldo_inicial: Decimal
    fecha_cierre: datetime | None
    saldo_esperado: Decimal | None
    saldo_contado: Decimal | None
    diferencia: Decimal | None
    estado: str


class ArqueoLeer(BaseModel):
    """Cuadre EN VIVO de la caja abierta (misma fórmula que el cierre: fuente única, no se recalcula en
    el cliente). `saldo_esperado = saldo_inicial + ventas_efectivo + ingresos − egresos` (los egresos ya
    incluyen los gastos). Con la caja cerrada, `estado='cerrada'` y los componentes van en 0."""

    estado: str                              # 'abierta' | 'cerrada'
    caja_id: int | None = None
    fecha_apertura: datetime | None = None
    saldo_inicial: Decimal = Decimal(0)
    ventas_efectivo: Decimal = Decimal(0)
    ingresos: Decimal = Decimal(0)           # movimientos manuales de ingreso
    egresos: Decimal = Decimal(0)            # movimientos de egreso (incluye los gastos)
    saldo_esperado: Decimal = Decimal(0)


class EstadoCajaLeer(BaseModel):
    """Estado liviano para el guard del POS (`GET /caja/estado`): ¿se puede cobrar ya?

    En modo empresa (`caja_obligatoria`) refleja LA caja abierta de la empresa (sin importar quién la
    abrió); si no, la del usuario del request. Siempre 200: `abierta=false` es un estado, no un error."""

    abierta: bool
    caja_id: int | None = None
    saldo_inicial: Decimal | None = None
    fecha_apertura: datetime | None = None


class MovimientoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    caja_id: int
    tipo: str
    monto: Decimal
    concepto: str | None
    referencia: str | None
    creado_en: datetime


class GastoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    categoria: str
    monto: Decimal
    concepto: str | None
    caja_id: int | None
    usuario_id: int | None
    proveedor_id: int | None = None
    factura_proveedor_id: str | None = None
    abono_proveedor_id: int | None = None
    creado_en: datetime
    # --- Vertical construcción (spec 09). Default para las filas del POS (backward-compatible). ---
    obra_id: int | None = None
    maquina_id: int | None = None
    categoria_gasto: str | None = None
    metodo_pago: str | None = None
    numero_referencia: str | None = None
    comprobante_url: str | None = None
    origen_registro: str = "MANUAL"
    telegram_user_id: str | None = None
    telegram_message_id: str | None = None
    requiere_revision: bool = False
    # Rechazo de la bandeja (0056): NULL = vivo. La reversa de caja está asentada como ingreso inverso.
    anulado_en: datetime | None = None
    motivo_rechazo: str | None = None


class GastoRechazar(BaseModel):
    """Body del POST /gastos/{id}/rechazar: motivo opcional del rechazo (lo escribe el admin)."""

    motivo: str | None = Field(default=None, max_length=500)


class GastoImputacionPatch(BaseModel):
    """PATCH /gastos/{id}/imputacion — re-imputar un gasto PENDIENTE antes de aprobarlo.

    Solo destino/clasificación: obra, máquina, categoría del vertical y concepto. NUNCA el monto (su
    egreso de caja ya está posteado; cambiar plata = rechazar y registrar el gasto correcto)."""

    obra_id: int | None = None
    maquina_id: int | None = None
    categoria_gasto: CategoriaGastoVertical | None = None
    concepto: str | None = Field(default=None, max_length=300)
