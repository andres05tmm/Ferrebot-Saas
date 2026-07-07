"""Schemas Pydantic de la cartera de alquiler (cupos, config, vistas de consumo). Valida toda entrada.

El `cupo` va en MONEY4 (18,4, vertical construcción). El `consumido`/`disponible` son DERIVADOS del
ledger de fiados (`clientes.saldo_fiado`) —no se guardan en `cupos_alquiler` (diseño §1.2)—: los computa
el service al leer, no viven en la tabla. El `semaforo` y el chip `colita` los deriva el service en vivo.
"""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CupoCrear(BaseModel):
    """Alta de cupo. Crear un cupo DESACTIVA el activo previo del cliente (un solo cupo activo)."""

    cliente_id: int
    cupo: Decimal = Field(gt=0, description="Tope de crédito de alquiler (MONEY4)")
    vigente_desde: date
    vigente_hasta: date | None = None
    notas: str | None = None


class CupoActualizar(BaseModel):
    """Edición PARCIAL de un cupo (solo los campos enviados: `model_dump(exclude_unset=True)`)."""

    cupo: Decimal | None = Field(default=None, gt=0)
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activo: bool | None = None
    notas: str | None = None


class CupoLeer(BaseModel):
    """Fila de la tabla de cupos del dashboard: el cupo + su consumo/disponible/semáforo en vivo.

    `consumido` = `clientes.saldo_fiado` (ledger); `disponible` = `cupo − consumido`. `semaforo`:
    verde (disponible > 20% del cupo), amarillo (0–20%), rojo (excedido). `colita` = el cliente tiene
    una obra finalizada/liquidada con saldo estancado (sin abono > N días).
    """

    id: int
    cliente_id: int
    cliente_nombre: str | None = None
    cupo: Decimal
    vigente_desde: date
    vigente_hasta: date | None
    activo: bool
    notas: str | None
    consumido: Decimal
    disponible: Decimal
    semaforo: str
    colita: bool = False


class CarteraConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activo: bool
    dias_colita: int
    cadencia_aviso_dias: int


class CarteraConfigActualizar(BaseModel):
    """Config de la detección de colitas. Límites: guardarraíl contra valores absurdos (se validan al
    setear, no se confía en la BD)."""

    activo: bool = True
    dias_colita: int = Field(default=15, ge=1, le=365)
    cadencia_aviso_dias: int = Field(default=7, ge=1, le=90)


class CargoObraLeer(BaseModel):
    """Un cargo de alquiler imputado a la obra (traza `RegistroHorasMaquina` → `Fiado`).

    `maquina_nombre` y `horas_facturables` son de LECTURA (JOIN a `maquinas` y a
    `registros_horas_maquina`): el dashboard los muestra en el detalle de cargos por obra; sin ellos
    caería a "Máquina #id" y "0 h". Nullable por si la máquina o el registro se borraron (LEFT JOIN)."""

    id: int
    registro_horas_id: int
    fiado_id: int
    maquina_id: int
    maquina_nombre: str | None = None
    asignacion_id: int
    horas_facturables: Decimal | None = None
    monto: Decimal
    fiado_saldo: Decimal
    creado_en: datetime


class AbonoCarteraLeer(BaseModel):
    """Un abono del ledger imputable a la obra (movimiento `abono` de un fiado enlazado por sus cargos)."""

    id: int
    monto: Decimal
    fecha: datetime


class ObraCarteraLeer(BaseModel):
    """Detalle de cartera de una obra (vista de liquidación): saldo pendiente + sus cargos + abonos.

    `obra_nombre`/`cliente_nombre` son de LECTURA (JOIN a `obras`/`clientes`); el dashboard cae a "#id"
    sin ellos. `abonos` = los abonos del ledger de los fiados enlazados por los cargos de ESTA obra
    (cada cargo crea su propio fiado, así que el abono queda atribuido a la obra, no solo al cliente)."""

    obra_id: int
    cliente_id: int
    obra_nombre: str | None = None
    cliente_nombre: str | None = None
    saldo: Decimal
    cargos: list[CargoObraLeer]
    abonos: list[AbonoCarteraLeer] = []


class ColitaLeer(BaseModel):
    """Colita detectada (para el semáforo del dashboard): cliente con saldo estancado en obra cerrada.

    `cliente_nombre`/`obra_nombre` son de LECTURA (JOIN a `clientes`/`obras`): el dashboard cae a
    "Cliente #id"/"Obra #id" sin ellos."""

    cliente_id: int
    obra_id: int
    cliente_nombre: str | None = None
    obra_nombre: str | None = None
    saldo: Decimal
    dias_sin_abono: int
    ultimo_abono_en: datetime | None
