"""Contratos Pydantic de la cola de impresión (ADR 0033). Validación de toda entrada."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrabajoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: str
    payload: dict
    zona_id: int | None
    ancho: int | None
    estado: str
    intentos: int
    error_detalle: str | None
    pedido_id: int | None
    comanda_id: int | None
    venta_id: int | None
    reimpresion_de: int | None
    creado_en: datetime


class AckTrabajo(BaseModel):
    ok: bool
    detalle: str | None = Field(default=None, max_length=500)


class DispositivoCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)


class CrearTrabajo(BaseModel):
    """Trabajo bajo demanda: precuenta de un pedido o comprobante de una venta."""

    tipo: Literal["precuenta", "comprobante"]
    pedido_id: int | None = Field(default=None, gt=0)
    venta_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _origen(self) -> "CrearTrabajo":
        if self.tipo == "precuenta" and self.pedido_id is None:
            raise ValueError("precuenta requiere pedido_id")
        if self.tipo == "comprobante" and self.venta_id is None:
            raise ValueError("comprobante requiere venta_id")
        return self
