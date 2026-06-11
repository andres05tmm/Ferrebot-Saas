"""Schemas Pydantic del frente de pagos (dashboard)."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CobroLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    referencia: str
    origen: str
    origen_id: int | None
    cliente_telefono: str | None
    monto: Decimal
    descripcion: str | None
    estado: str
    proveedor: str
    url: str | None
    creado_en: datetime
    actualizado_en: datetime
