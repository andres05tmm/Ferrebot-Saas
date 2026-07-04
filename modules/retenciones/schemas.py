"""Contratos Pydantic de retenciones/INC (ADR 0027)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

TIPOS_VALIDOS = {"retefuente", "ica", "reteiva", "inc", "uvt"}


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
    """Resultado de aplicar el motor a un documento: renglones + neto (el total NO se toca).

    `total_documento` es el total cobrado/facturado, INTACTO. `total_retenido` (retefuente/ica/reteiva)
    reduce el pago recibido: `neto_a_recibir = total_documento − total_retenido`. `total_inc` se informa
    aparte (impuesto al consumo registrado; su incorporación al total es opt-in futuro, ADR 0027).
    """

    doc_tipo: str
    doc_id: int
    total_documento: Decimal
    total_retenido: Decimal
    total_inc: Decimal
    neto_a_recibir: Decimal
    retenciones: list[RetencionLeer]
