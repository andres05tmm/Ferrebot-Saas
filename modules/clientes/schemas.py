"""Contratos Pydantic de clientes (schema.md / tenant 0001).

`crear_cliente` mínimo (ai-tools.md §5.4): el modelo solo manda datos básicos; los campos
fiscales (`ciudad_dane`, `regimen`) son condicionales a la feature `facturacion_electronica`
y por eso quedan opcionales aquí (el riel/feature flag decide si se piden, feature-flags.md).
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TipoDocumento = Literal["CC", "NIT", "CE", "TI", "PAS", "NUIP"]


class ClienteCrear(BaseModel):
    nombre: str = Field(min_length=1)
    tipo_documento: TipoDocumento | None = None
    documento: str | None = None
    telefono: str | None = None
    correo: str | None = None
    direccion: str | None = None
    ciudad_dane: str | None = None
    regimen: str | None = None


class ClienteLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tipo_documento: str | None
    documento: str | None
    telefono: str | None
    correo: str | None
    direccion: str | None
    ciudad_dane: str | None
    regimen: str | None
    saldo_fiado: Decimal
    creado_en: datetime
