"""Contratos Pydantic de retenciones/INC (ADR 0027)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# Reglas de cálculo + filas de CONFIG: `uvt` (valor del UVT) e `inc_al_total` (interruptor opt-in que
# suma el INC al total del documento, ADR 0027 D5). Las de config no generan renglón (ver motor).
TIPOS_VALIDOS = {"retefuente", "ica", "reteiva", "inc", "uvt", "inc_al_total"}


class ReglaUpsert(BaseModel):
    """Alta/edición de una regla del catálogo tributario (config_retenciones)."""

    tipo: str = Field(description="retefuente | ica | reteiva | inc | uvt")
    concepto: str = Field(min_length=1, max_length=120)
    base_minima_uvt: Decimal = Field(default=Decimal("0"), ge=0)
    tarifa: Decimal = Field(default=Decimal("0"), ge=0)
    activo: bool = True


class ReglaLeer(BaseModel):
    """Una regla del catálogo tal como se lee (config_retenciones)."""

    id: int
    tipo: str
    concepto: str
    base_minima_uvt: Decimal
    tarifa: Decimal
    activo: bool
    editable: bool
    actualizado_en: datetime | None = None


class RetencionLeer(BaseModel):
    """Un renglón tributario persistido de un documento (retenciones_documento)."""

    tipo: str
    concepto: str
    base: Decimal
    tarifa: Decimal
    valor: Decimal


class ResumenRetenciones(BaseModel):
    """Resultado de aplicar el motor a un documento: renglones + neto (la tabla `ventas` NO se toca).

    `total_documento` es el total cobrado/facturado en la tabla, SIEMPRE intacto (invariante ADR 0027:
    el motor jamás muta `ventas.total`). `total_retenido` (retefuente/ica/reteiva) reduce el pago
    recibido. El INC es distinto: SUMA al total cuando el tenant activa la config `inc_al_total`
    (`inc_al_total=True`, ADR 0027 D5); si no, se informa aparte como antes. `total_con_inc` es el total
    del documento a nivel fiscal ( `total_documento + total_inc` con el interruptor activo, si no
    `total_documento`); `neto_a_recibir = total_con_inc − total_retenido`.
    """

    doc_tipo: str
    doc_id: int
    total_documento: Decimal
    total_retenido: Decimal
    total_inc: Decimal
    total_con_inc: Decimal
    inc_al_total: bool
    neto_a_recibir: Decimal
    retenciones: list[RetencionLeer]
