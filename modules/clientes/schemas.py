"""Contratos Pydantic de clientes (schema.md / tenant 0001, extendido por construcción en 0046).

`crear_cliente` mínimo (ai-tools.md §5.4): el modelo solo manda datos básicos; los campos
fiscales (`ciudad_dane`, `regimen`) son condicionales a la feature `facturacion_electronica`
y por eso quedan opcionales aquí (el riel/feature flag decide si se piden, feature-flags.md).

El vertical construcción (spec 02 / tenant 0046) suma un mini-CRM OPCIONAL: `estatus`
(PROSPECTO→MOROSO), datos de `contacto_*` y un `acuerdo_comercial` de texto libre. Se exponen aquí
como campos NULLABLE con default None → backward-compatible: el POS/retail no los manda y su contrato
no cambia. La sugerencia AUTOMÁTICA de MOROSO es de una fase posterior; aquí `estatus` solo se
expone/persiste tal cual lo mande el llamador.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TipoDocumento = Literal["CC", "NIT", "CE", "TI", "PAS", "NUIP"]
# Literales EXACTOS al enum `estatus_cliente` (tenant 0046). Solo para la ENTRADA (validación de
# `ClienteCrear`); la lectura usa `str | None` (el valor viene de la BD, ya válido) — mismo criterio
# que `TipoDocumento`, que valida al crear pero se lee como `str | None`.
EstatusCliente = Literal["PROSPECTO", "ACTIVO", "RECURRENTE", "INACTIVO", "MOROSO"]


class ClienteCrear(BaseModel):
    nombre: str = Field(min_length=1)
    tipo_documento: TipoDocumento | None = None
    documento: str | None = None
    telefono: str | None = None
    correo: str | None = None
    direccion: str | None = None
    ciudad_dane: str | None = None
    regimen: str | None = None

    # --- Mini-CRM construcción (spec 02 / tenant 0046). OPCIONALES, backward-compatible. ---
    # `estatus` None NO se persiste como NULL: el repositorio lo omite del INSERT para que aplique el
    # server_default 'PROSPECTO' de la migración 0046 (default de la spec).
    estatus: EstatusCliente | None = None
    contacto_nombre: str | None = None
    contacto_cargo: str | None = None
    contacto_telefono: str | None = None
    contacto_email: str | None = None
    acuerdo_comercial: str | None = None


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

    # --- Mini-CRM construcción (tenant 0046). Default None → un cliente POS sin estos datos los devuelve
    # como null (y `from_attributes` cae al default si el objeto no trae el atributo, p. ej. en tests). ---
    estatus: str | None = None
    contacto_nombre: str | None = None
    contacto_cargo: str | None = None
    contacto_telefono: str | None = None
    contacto_email: str | None = None
    acuerdo_comercial: str | None = None
