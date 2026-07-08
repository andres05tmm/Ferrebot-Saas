"""Contratos Pydantic de trabajadores (vertical construcción, spec cliente 07_EMPLEADOS / tenant 0043).

Los campos calcan las columnas del ORM (`modules.trabajadores.models.Trabajador`) con sus nombres en
español TAL CUAL. La spec distingue dos naturalezas por `tipo_vinculacion`: DIRECTO (planta, con
salario y seguridad social) vs PATACALIENTE (por hora, con `tarifa_hora`). La validación condicional
"si DIRECTO exige salario / si PATACALIENTE exige tarifa" es de la Fase de nómina (08); aquí el CRUD
base deja ambos bloques opcionales para no bloquear altas parciales.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TipoVinculacion = Literal["DIRECTO", "PATACALIENTE"]


class TrabajadorCrear(BaseModel):
    """Alta de un trabajador. `documento` es la clave natural (UNIQUE en la base)."""

    tipo_vinculacion: TipoVinculacion
    documento: str = Field(min_length=1)
    tipo_documento: str = "CC"
    nombres: str = Field(min_length=1)
    apellidos: str = Field(min_length=1)
    cargo: str = Field(min_length=1)
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    fecha_ingreso: date | None = None
    fecha_retiro: date | None = None
    activo: bool = True

    # Solo DIRECTO (opcionales en el CRUD base; la validación condicional es de nómina).
    salario_base: Decimal | None = Field(default=None, ge=0)
    aplica_aux_transporte: bool = True
    eps: str | None = None
    fondo_pension: str | None = None
    arl: str | None = None
    caja_compensacion: str | None = None
    cuenta_bancaria: str | None = None
    banco_nombre: str | None = None

    # Solo PATACALIENTE.
    tarifa_hora: Decimal | None = Field(default=None, ge=0)


class TrabajadorActualizar(BaseModel):
    """Edición parcial (PATCH): solo los campos presentes se aplican (`exclude_unset` en el router).

    `tipo_vinculacion` y `documento` se pueden corregir; el cambio de documento vuelve a validar la
    unicidad en el servicio. No incluye `eliminado_en`: la baja va por el DELETE (soft delete).
    """

    tipo_vinculacion: TipoVinculacion | None = None
    documento: str | None = Field(default=None, min_length=1)
    tipo_documento: str | None = None
    nombres: str | None = Field(default=None, min_length=1)
    apellidos: str | None = Field(default=None, min_length=1)
    cargo: str | None = Field(default=None, min_length=1)
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    fecha_ingreso: date | None = None
    fecha_retiro: date | None = None
    activo: bool | None = None
    salario_base: Decimal | None = Field(default=None, ge=0)
    aplica_aux_transporte: bool | None = None
    eps: str | None = None
    fondo_pension: str | None = None
    arl: str | None = None
    caja_compensacion: str | None = None
    cuenta_bancaria: str | None = None
    banco_nombre: str | None = None
    tarifa_hora: Decimal | None = Field(default=None, ge=0)


class TrabajadorLeer(BaseModel):
    """Vista de salida de un trabajador (todas las columnas del ORM)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_vinculacion: str
    documento: str
    tipo_documento: str
    nombres: str
    apellidos: str
    cargo: str
    telefono: str | None
    email: str | None
    direccion: str | None
    fecha_ingreso: date | None
    fecha_retiro: date | None
    activo: bool
    salario_base: Decimal | None
    aplica_aux_transporte: bool
    eps: str | None
    fondo_pension: str | None
    arl: str | None
    caja_compensacion: str | None
    cuenta_bancaria: str | None
    banco_nombre: str | None
    tarifa_hora: Decimal | None
    creado_en: datetime
    actualizado_en: datetime
