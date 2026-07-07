"""Contratos Pydantic del cotizador AIU (vertical construcción, spec cliente 03 / tenant 0044).

Los campos calcan las columnas del ORM (`modules.obra.models.CotizacionObra` / `ItemCotizacionObra`)
con sus nombres en español TAL CUAL. Los porcentajes AIU son FRACCIONES 0–1 (0.05 = 5%), como se
guardan en la tabla; el desglose AIU (administración/imprevistos/utilidad/iva) NO viaja en el request
—lo calcula SIEMPRE `services.calculations.aiu.calcular_totales_cotizacion` (una sola fuente de verdad,
money-safe)— y se expone en la lectura dentro de `totales`.

El `numero` es opcional al crear: si no se envía, el servicio autogenera el consecutivo
`PIM-0XX-AAAA`; si se envía, se respeta (editable, spec 03) y su unicidad la garantiza la BD.
El `estado` no se fija ni se edita por el builder: arranca BORRADOR (default de la base) y cambia
sólo por el endpoint dedicado `/estado`, que valida el ciclo de vida.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EstadoCotizacion = Literal["BORRADOR", "ENVIADA", "GANADA", "PERDIDA", "VENCIDA"]

# Un ítem debe tener cantidad y valor no negativos; los porcentajes AIU son fracciones 0–1.
_PCT = Field(default=Decimal("0"), ge=0, le=1)


class ItemCotizacionObraCrear(BaseModel):
    """Renglón del builder (entrada). El `orden` lo fija el cliente (arrastrar para reordenar)."""

    orden: int = Field(ge=0)
    descripcion: str = Field(min_length=1)
    unidad: str = Field(min_length=1)
    cantidad: Decimal = Field(ge=0)
    valor_unitario: Decimal = Field(ge=0)
    # Desglose de costo interno estimado (oculto al cliente; alimenta el presupuesto de obra). Opcional.
    costo_material_est: Decimal | None = Field(default=None, ge=0)
    costo_mano_obra_est: Decimal | None = Field(default=None, ge=0)
    costo_equipo_est: Decimal | None = Field(default=None, ge=0)


class ItemCotizacionObraLeer(BaseModel):
    """Renglón de salida: incluye el `id` y el `subtotal` del renglón (cantidad × valor_unitario)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    orden: int
    descripcion: str
    unidad: str
    cantidad: Decimal
    valor_unitario: Decimal
    subtotal: Decimal
    costo_material_est: Decimal | None
    costo_mano_obra_est: Decimal | None
    costo_equipo_est: Decimal | None


class CotizacionObraCrear(BaseModel):
    """Alta de una cotización AIU con sus ítems dinámicos (borrador)."""

    numero: str | None = None  # None → consecutivo autogenerado PIM-0XX-AAAA
    cliente_id: int
    nombre_obra: str = Field(min_length=1)
    ubicacion: str | None = None
    vigencia_dias: int = Field(default=15, ge=0)
    administracion_pct: Decimal = _PCT
    imprevistos_pct: Decimal = _PCT
    utilidad_pct: Decimal = _PCT
    iva_sobre_utilidad_pct: Decimal = Field(default=Decimal("0.19"), ge=0, le=1)
    condiciones: str | None = None
    items: list[ItemCotizacionObraCrear] = Field(default_factory=list)


class CotizacionObraActualizar(BaseModel):
    """Edición del builder (PUT: reemplaza el conjunto de ítems). NO cambia `numero` ni `estado`.

    `items` presente reemplaza TODOS los renglones (semántica de builder: el front manda el set
    completo); ausente los deja igual. Los demás campos ausentes no se tocan (parche parcial).
    """

    cliente_id: int | None = None
    nombre_obra: str | None = Field(default=None, min_length=1)
    ubicacion: str | None = None
    vigencia_dias: int | None = Field(default=None, ge=0)
    administracion_pct: Decimal | None = Field(default=None, ge=0, le=1)
    imprevistos_pct: Decimal | None = Field(default=None, ge=0, le=1)
    utilidad_pct: Decimal | None = Field(default=None, ge=0, le=1)
    iva_sobre_utilidad_pct: Decimal | None = Field(default=None, ge=0, le=1)
    condiciones: str | None = None
    items: list[ItemCotizacionObraCrear] | None = None


class CotizacionObraEstadoCambiar(BaseModel):
    """Solicitud de transición de estado (el servicio valida que sea permitida)."""

    estado: EstadoCotizacion


class TotalesAIULeer(BaseModel):
    """Desglose AIU de salida (money-safe): subtotal + A/I/U + IVA sobre la utilidad + total.

    Espeja `services.calculations.aiu.TotalesAIU`. El IVA grava SÓLO la utilidad (regla del cliente).
    """

    model_config = ConfigDict(from_attributes=True)

    subtotal: Decimal
    administracion: Decimal
    imprevistos: Decimal
    utilidad: Decimal
    iva_utilidad: Decimal
    total: Decimal


class CotizacionObraLeer(BaseModel):
    """Vista completa de una cotización AIU: cabecera + ítems + desglose AIU calculado."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    numero: str
    cliente_id: int
    nombre_obra: str
    ubicacion: str | None
    fecha_emision: datetime
    vigencia_dias: int
    administracion_pct: Decimal
    imprevistos_pct: Decimal
    utilidad_pct: Decimal
    iva_sobre_utilidad_pct: Decimal
    estado: str
    condiciones: str | None
    creado_en: datetime
    actualizado_en: datetime
    items: list[ItemCotizacionObraLeer]
    totales: TotalesAIULeer


class CotizacionObraResumen(BaseModel):
    """Fila de la lista (sin los ítems, con el total del contrato para el panorama)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    numero: str
    cliente_id: int
    nombre_obra: str
    ubicacion: str | None
    fecha_emision: datetime
    vigencia_dias: int
    estado: str
    creado_en: datetime
    actualizado_en: datetime
    total: Decimal
