"""Contratos Pydantic de maquinaria (spec cliente 05_MAQUINAS — tenant 0043/0045).

Los nombres de campo son EXACTOS a las columnas del ORM (`modules.maquinaria.models`): el contrato de
la fase fija "campos JSON = nombres de columna en español tal cual el ORM". Dinero en `Decimal`
(MONEY4 en la BD). El alta exige los NOT NULL de la spec; la edición (PATCH) es parcial y todos sus
campos son opcionales (solo se tocan los enviados, ver `service.actualizar`).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Literales EXACTOS al enum `estado_maquina` (migración 0043). Validar aquí evita un INSERT que la BD
# rechazaría por el tipo enum, devolviendo 422 en vez de 500.
EstadoMaquina = Literal["DISPONIBLE", "OCUPADA", "MANTENIMIENTO", "DAÑADA", "BAJA"]


class MaquinaCrear(BaseModel):
    """Alta de una máquina. `codigo`/`nombre`/`tipo`/`precio_hora_default` son NOT NULL en la spec."""

    codigo: str = Field(min_length=1)
    nombre: str = Field(min_length=1)
    tipo: str = Field(min_length=1)
    placa: str | None = None
    serial: str | None = None
    anio_fabricacion: int | None = Field(default=None, ge=1900, le=2200)
    estado: EstadoMaquina = "DISPONIBLE"
    precio_hora_default: Decimal = Field(ge=0)   # valor sugerido de facturación por hora
    minimo_horas_factura: int = Field(default=1, ge=0)   # piso facturable por servicio
    costo_operacion_hora: Decimal | None = Field(default=None, ge=0)
    operador_asignado_id: int | None = None
    foto_url: str | None = None
    notas: str | None = None


class MaquinaActualizar(BaseModel):
    """Edición PARCIAL (PATCH): solo los campos presentes en el cuerpo se aplican (`exclude_unset`).

    Todos opcionales; los que se envíen conservan las mismas validaciones del alta. `codigo=null` no es
    válido (min_length lo rechaza) porque es NOT NULL; los nullables sí aceptan `null` para limpiarse.
    """

    codigo: str | None = Field(default=None, min_length=1)
    nombre: str | None = Field(default=None, min_length=1)
    tipo: str | None = Field(default=None, min_length=1)
    placa: str | None = None
    serial: str | None = None
    anio_fabricacion: int | None = Field(default=None, ge=1900, le=2200)
    estado: EstadoMaquina | None = None
    precio_hora_default: Decimal | None = Field(default=None, ge=0)
    minimo_horas_factura: int | None = Field(default=None, ge=0)
    costo_operacion_hora: Decimal | None = Field(default=None, ge=0)
    operador_asignado_id: int | None = None
    foto_url: str | None = None
    notas: str | None = None


class MaquinaLeer(BaseModel):
    """Vista de salida de una máquina (todas las columnas del ORM, soft delete incluido)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    nombre: str
    tipo: str
    placa: str | None
    serial: str | None
    anio_fabricacion: int | None
    estado: str
    precio_hora_default: Decimal
    minimo_horas_factura: int
    costo_operacion_hora: Decimal | None
    operador_asignado_id: int | None
    foto_url: str | None
    notas: str | None
    creado_en: datetime
    actualizado_en: datetime
    eliminado_en: datetime | None


class AsignacionMaquinaObraLeer(BaseModel):
    """Lectura de una asignación de máquina a obra (solo lectura; el alta/edición es de Fase 3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    maquina_id: int
    obra_id: int
    fecha_inicio: date
    fecha_fin: date | None
    precio_hora: Decimal
    minimo_horas: int
    operador_id: int | None
    activa: bool


class RegistroHorasMaquinaLeer(BaseModel):
    """Lectura de un parte de horas de una máquina (solo lectura; el registro es de Fase 3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    maquina_id: int
    obra_id: int
    fecha: date
    horas_trabajadas: Decimal
    horas_facturables: Decimal
    operador_id: int | None
    observaciones: str | None
    origen_registro: str
    creado_en: datetime
