"""Contratos Pydantic de maquinaria (spec cliente 05_MAQUINAS — tenant 0043/0045).

Los nombres de campo son EXACTOS a las columnas del ORM (`modules.maquinaria.models`): el contrato de
la fase fija "campos JSON = nombres de columna en español tal cual el ORM". Dinero en `Decimal`
(MONEY4 en la BD). El alta exige los NOT NULL de la spec; la edición (PATCH) es parcial y todos sus
campos son opcionales (solo se tocan los enviados, ver `service.actualizar`).
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.config.timezone import today_co

# Literales EXACTOS al enum `estado_maquina` (migración 0043). Validar aquí evita un INSERT que la BD
# rechazaría por el tipo enum, devolviendo 422 en vez de 500.
EstadoMaquina = Literal["DISPONIBLE", "OCUPADA", "MANTENIMIENTO", "DAÑADA", "BAJA"]

# Literales EXACTOS al enum `origen_registro` (dueño migración 0044; se reusa). MANUAL = dashboard;
# TELEGRAM_BOT = parte cargado por el bot de campo (Fase 6); IMPORTACION = ETL.
OrigenRegistro = Literal["MANUAL", "TELEGRAM_BOT", "IMPORTACION"]

# Literales EXACTOS al enum `tipo_mantenimiento` (migración 0045). Validar aquí devuelve 422 (no 500 por
# el enum de la BD). PREVENTIVO = programado por horómetro/fecha; CORRECTIVO = falla; INSPECCION = revisión.
TipoMantenimiento = Literal["PREVENTIVO", "CORRECTIVO", "INSPECCION"]


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


class AsignacionMaquinaCrear(BaseModel):
    """Alta de una asignación de máquina a obra (Calendario de obra). `maquina_id` viaja por la ruta.

    `fecha_inicio` es opcional: si falta, el service la resuelve a HOY en hora Colombia (regla #4). Igual
    `precio_hora`/`minimo_horas`: si no vienen, el service toma los defaults de la máquina
    (`precio_hora_default`/`minimo_horas_factura`). El validador exige `fecha_fin >= fecha_inicio` cuando
    ambas están presentes (else 422).
    """

    obra_id: int
    fecha_inicio: date | None = None   # default hoy Colombia en el service
    fecha_fin: date | None = None
    precio_hora: Decimal | None = Field(default=None, ge=0)   # default = maquina.precio_hora_default
    minimo_horas: int | None = Field(default=None, ge=0)      # default = maquina.minimo_horas_factura
    operador_id: int | None = None

    @model_validator(mode="after")
    def _rango_valido(self) -> "AsignacionMaquinaCrear":
        # Compara contra el default EFECTIVO (hoy Colombia) cuando fecha_inicio no viene: sin esto,
        # omitir fecha_inicio y mandar una fecha_fin en el pasado crearía un rango invertido.
        inicio = self.fecha_inicio or today_co()
        if self.fecha_fin is not None and self.fecha_fin < inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")
        return self


class AsignacionMaquinaActualizar(BaseModel):
    """Edición PARCIAL (PATCH) de una asignación: solo los campos presentes se aplican (`exclude_unset`).

    `fecha_fin=null` explícito es válido (reabre el rango); se distingue del "no enviado" por
    `exclude_unset` en el service. No incluye `obra_id` ni `fecha_inicio` (el contrato PATCH no los trae).
    """

    fecha_fin: date | None = None
    activa: bool | None = None
    operador_id: int | None = None
    precio_hora: Decimal | None = Field(default=None, ge=0)
    minimo_horas: int | None = Field(default=None, ge=0)


class TurnoLeer(BaseModel):
    """Franja de un operador dentro del parte del día (rotación de operadores). `operador` es el nombre
    resuelto del trabajador (None si el turno no tiene operador o el trabajador fue borrado)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    operador_id: int | None
    operador: str | None
    hora_inicio: time | None
    hora_fin: time | None
    horas: Decimal


class RegistroHorasMaquinaLeer(BaseModel):
    """Lectura de un parte de horas de una máquina (kárdex de operación).

    `turnos` lista las franjas de operador del día (rotación); es `[]` en los partes legacy sin turnos —el
    front cae al `operador_id` de cabecera."""

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
    turnos: list[TurnoLeer] = Field(default_factory=list)


class RegistroHorasCrear(BaseModel):
    """Alta de un parte de horas de una máquina en una obra (Fase 3). `maquina_id` viaja por la ruta.

    `idempotency_key` es OPCIONAL y existe para el contrato de reintentos del bot de campo (Fase 6). La
    idempotencia efectiva se ancla en la CLAVE NATURAL `(maquina_id, obra_id, fecha)` —la spec define un
    parte POR MÁQUINA POR DÍA—, no en una columna dedicada (models/migraciones son de otro agente). Ver
    el docstring de `MaquinariaService.registrar_horas` para el porqué y el seam de hardening.
    """

    obra_id: int
    fecha: date
    horas_trabajadas: Decimal = Field(ge=0)   # las horas son la unidad de negocio; no se redondean
    operador_id: int | None = None
    observaciones: str | None = None
    origen_registro: OrigenRegistro = "MANUAL"   # el bot de Fase 6 mandará TELEGRAM_BOT
    idempotency_key: str | None = Field(default=None, max_length=200)
    # Franja del turno (rotación de operadores). Opcional e informativa (las `horas` son la verdad); su
    # presencia —junto con `operador_id`— es lo que hace que el parte registre un TURNO en vez de quedar
    # como el parte legacy sin turnos. `time` acepta "HH:MM" del cliente.
    hora_inicio: time | None = None
    hora_fin: time | None = None


class RegistroHorasResultado(BaseModel):
    """Resumen de un registro de horas (salida del POST). `replay=True` = el parte de ese día YA existía
    (idempotencia por clave natural): no se creó un segundo registro."""

    model_config = ConfigDict(from_attributes=True)

    registro_id: int
    maquina_id: int
    obra_id: int
    fecha: date
    horas_trabajadas: Decimal
    horas_facturables: Decimal
    minimo_cubierto: bool                # ¿las horas trabajadas alcanzaron el mínimo pactado?
    precio_hora: Decimal                 # precio PACTADO en la asignación (no el default de la máquina)
    ingreso: Decimal                     # = horas_facturables × precio_hora (del DÍA, suma de turnos)
    origen_registro: str
    replay: bool
    turnos: list[TurnoLeer] = Field(default_factory=list)   # franjas de operador del día (rotación)


# --- Operación de máquina EN VIVO (cronómetro + rotación de operadores, migración 0055) --------------


class IniciarOperacion(BaseModel):
    """Activar una máquina. `maquina_id` viaja por la ruta. `obra_id` es opcional: si falta, el service
    infiere la asignación vigente hoy (por el invariante de no-solape hay a lo sumo una). `operador_id`
    es el primer operador del cronómetro (opcional: se puede activar sin asignar aún)."""

    obra_id: int | None = None
    operador_id: int | None = None


class RotarOperador(BaseModel):
    """Cambiar de operador en vivo: cierra el tramo corriente y abre otro. `operador_id` puede ser null
    (la máquina queda corriendo sin operador asignado en ese tramo)."""

    operador_id: int | None = None


class AjusteTramo(BaseModel):
    """Ajuste manual de las horas de un tramo al finalizar (el supervisor confirma/edita lo del reloj)."""

    tramo_id: int
    horas: Decimal = Field(ge=0)


class FinalizarOperacion(BaseModel):
    """Finalizar y materializar la sesión. `ajustes` pisa las horas medidas por el reloj tramo a tramo;
    los tramos no incluidos usan el tiempo medido (default)."""

    ajustes: list[AjusteTramo] = Field(default_factory=list)


class SesionLeer(BaseModel):
    """Vista de salida de una sesión de operación (todas las columnas del ORM)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    maquina_id: int
    obra_id: int
    asignacion_id: int
    fecha: date
    estado: str
    iniciada_en: datetime
    finalizada_en: datetime | None
    registro_horas_id: int | None
    notas: str | None


class TramoDetalle(BaseModel):
    """Tramo de una sesión con el operador resuelto y las horas PROPUESTAS (el reloj propone, el humano
    ajusta). Lo consume el modal de revisión al finalizar."""

    id: int
    operador_id: int | None
    operador: str | None
    iniciado_en: datetime
    finalizado_en: datetime | None
    horas_propuestas: Decimal


class SesionDetalle(SesionLeer):
    """Sesión + sus tramos (para el modal de revisión). Extiende `SesionLeer` con el desglose de rotación."""

    tramos: list[TramoDetalle] = Field(default_factory=list)


class TableroSesion(BaseModel):
    """Fila del tablero en vivo: una sesión ABIERTA con nombres de máquina/obra y el operador/inicio del
    tramo corriente. Alimenta las tarjetas con cronómetro (el front cuenta desde `tramo_desde`/`iniciada_en`)."""

    sesion_id: int
    maquina_id: int
    maquina: str
    obra_id: int
    obra: str
    iniciada_en: datetime
    operador_id: int | None
    operador: str | None
    tramo_desde: datetime | None


# --- Mantenimientos (Fase 1 del cockpit): CRUD sobre la tabla de la migración 0045 ------------------


class MantenimientoCrear(BaseModel):
    """Alta de un mantenimiento de una máquina. `maquina_id` viaja por la ruta.

    `descripcion` es NOT NULL en la spec (min_length ≥ 1). `costo` es NOT NULL en la BD: default 0 (una
    inspección puede no costar). `fecha` es opcional aquí y se resuelve a HOY en hora Colombia en el
    service (regla #4: nunca `date.today()` crudo). `proximo_en_horas`/`proximo_en_fecha` programan el
    siguiente servicio (alimentan la alerta de mantenimiento vencido/próximo del dashboard, Fase 2).
    """

    tipo: TipoMantenimiento
    fecha: date | None = None   # default hoy Colombia en el service
    horas_maquina: Decimal | None = Field(default=None, ge=0)   # horómetro al momento
    descripcion: str = Field(min_length=1)
    costo: Decimal = Field(default=Decimal("0"), ge=0)
    proveedor_id: int | None = None
    proximo_en_horas: Decimal | None = Field(default=None, ge=0)   # preventivos: cada X horas
    proximo_en_fecha: date | None = None
    factura_url: str | None = None


class MantenimientoActualizar(BaseModel):
    """Edición PARCIAL (PATCH): solo los campos presentes en el cuerpo se aplican (`exclude_unset`).

    Todos opcionales; los enviados conservan las validaciones del alta. Los campos NOT NULL de la BD
    (`tipo`/`descripcion`/`costo`) no admiten `null` de negocio (mismo criterio que `MaquinaActualizar`).
    """

    tipo: TipoMantenimiento | None = None
    fecha: date | None = None
    horas_maquina: Decimal | None = Field(default=None, ge=0)
    descripcion: str | None = Field(default=None, min_length=1)
    costo: Decimal | None = Field(default=None, ge=0)
    proveedor_id: int | None = None
    proximo_en_horas: Decimal | None = Field(default=None, ge=0)
    proximo_en_fecha: date | None = None
    factura_url: str | None = None


class MantenimientoLeer(BaseModel):
    """Vista de salida de un mantenimiento (todas las columnas del ORM)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    maquina_id: int
    tipo: str
    fecha: date
    horas_maquina: Decimal | None
    descripcion: str
    costo: Decimal
    proveedor_id: int | None
    proximo_en_horas: Decimal | None
    proximo_en_fecha: date | None
    factura_url: str | None
    creado_en: datetime
