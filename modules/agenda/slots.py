"""Cálculo de cupos del pack Agenda/Citas — lógica PURA, sin I/O (patrón inventario/precios.py).

Aquí vive el cómputo determinista del motor: a partir de las ventanas de trabajo de un recurso, las
ocupaciones (citas + bloqueos ya expandidos a intervalos) y las reglas del negocio, devuelve los
cupos libres. No toca la BD ni la sesión — el servicio arma los insumos (vía el repo) y este módulo
solo calcula, así se testea el cálculo (granularidad, buffers, anticipación, ventana, capacidad,
zona horaria) sin Postgres.

Todo el tiempo se opera en hora Colombia (`COLOMBIA_TZ`, regla no negociable #4). Los datetimes que
entran y salen son *aware*; las `time`/`date` de la disponibilidad se combinan con la zona Colombia.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from core.config.timezone import COLOMBIA_TZ


@dataclass(frozen=True, slots=True)
class Intervalo:
    """Tramo de tiempo [inicio, fin) aware. Sirve para ventanas de trabajo y ocupaciones."""

    inicio: datetime
    fin: datetime

    def solapa(self, otro: "Intervalo") -> bool:
        """¿Se cruzan? Comparación semiabierta: tocarse en el borde NO es solaparse."""
        return self.inicio < otro.fin and otro.inicio < self.fin


@dataclass(frozen=True, slots=True)
class HorarioSemanal:
    """Una franja del horario semanal de un recurso (0=lunes … 6=domingo)."""

    dia_semana: int
    hora_inicio: time
    hora_fin: time


@dataclass(frozen=True, slots=True)
class ReglasCupo:
    """Subconjunto de `agenda_config` que necesita el cálculo de cupos."""

    intervalo_slots_min: int = 15
    anticipacion_minima_min: int = 120
    ventana_maxima_dias: int = 30
    capacidad_por_slot: int = 1


def expandir_ventanas(
    horarios: list[HorarioSemanal], desde: date, hasta: date
) -> list[Intervalo]:
    """Materializa el horario semanal en ventanas concretas [desde, hasta] (días inclusive).

    Por cada día del rango toma las franjas cuyo `dia_semana` coincide (weekday() de Python: lunes=0)
    y las combina con la zona Colombia. Varias franjas por día (mañana/tarde) → varias ventanas.
    """
    ventanas: list[Intervalo] = []
    dia = desde
    while dia <= hasta:
        for h in horarios:
            if h.dia_semana == dia.weekday() and h.hora_inicio < h.hora_fin:
                ventanas.append(
                    Intervalo(
                        datetime.combine(dia, h.hora_inicio, tzinfo=COLOMBIA_TZ),
                        datetime.combine(dia, h.hora_fin, tzinfo=COLOMBIA_TZ),
                    )
                )
        dia += timedelta(days=1)
    return ventanas


def _footprint(
    inicio: datetime, *, duracion_min: int, buffer_antes_min: int, buffer_despues_min: int
) -> Intervalo:
    """Huella que ocupa una cita en la agenda: la duración más sus buffers de preparación/limpieza."""
    return Intervalo(
        inicio - timedelta(minutes=buffer_antes_min),
        inicio + timedelta(minutes=duracion_min + buffer_despues_min),
    )


def _cupos_de_ventana(
    ventana: Intervalo,
    *,
    duracion_min: int,
    buffer_antes_min: int,
    buffer_despues_min: int,
    intervalo_slots_min: int,
) -> list[datetime]:
    """Cupos candidatos dentro de una ventana: rejilla de `intervalo_slots_min` cuya huella cabe entera.

    La rejilla se ancla al inicio de la ventana. Un candidato es válido si su huella completa
    (buffers incluidos) cae dentro de la ventana — la preparación/limpieza no se sale del horario.
    """
    paso = timedelta(minutes=intervalo_slots_min)
    candidatos: list[datetime] = []
    actual = ventana.inicio
    while actual <= ventana.fin:
        huella = _footprint(
            actual,
            duracion_min=duracion_min,
            buffer_antes_min=buffer_antes_min,
            buffer_despues_min=buffer_despues_min,
        )
        if huella.inicio >= ventana.inicio and huella.fin <= ventana.fin:
            candidatos.append(actual)
        actual += paso
    return candidatos


def calcular_slots(
    *,
    ventanas: list[Intervalo],
    ocupaciones: list[Intervalo],
    duracion_min: int,
    buffer_antes_min: int,
    buffer_despues_min: int,
    reglas: ReglasCupo,
    ahora: datetime,
) -> list[datetime]:
    """Cupos libres de un recurso: rejilla por ventana − ocupaciones, filtrados por las reglas.

    Descarta lo que viole `anticipacion_minima_min` (muy pronto) o `ventana_maxima_dias` (muy lejos),
    y deja un cupo solo si las citas/bloqueos que solapan su huella son menos que `capacidad_por_slot`
    (>1 habilita citas de grupo). Devuelve los inicios ordenados y sin repetir.
    """
    minimo = ahora + timedelta(minutes=reglas.anticipacion_minima_min)
    maximo = ahora + timedelta(days=reglas.ventana_maxima_dias)

    libres: set[datetime] = set()
    for ventana in ventanas:
        for inicio in _cupos_de_ventana(
            ventana,
            duracion_min=duracion_min,
            buffer_antes_min=buffer_antes_min,
            buffer_despues_min=buffer_despues_min,
            intervalo_slots_min=reglas.intervalo_slots_min,
        ):
            if inicio < minimo or inicio > maximo:
                continue
            huella = _footprint(
                inicio,
                duracion_min=duracion_min,
                buffer_antes_min=buffer_antes_min,
                buffer_despues_min=buffer_despues_min,
            )
            ocupados = sum(1 for o in ocupaciones if huella.solapa(o))
            if ocupados < reglas.capacidad_por_slot:
                libres.add(inicio)
    return sorted(libres)


def cupo_disponible(
    *,
    inicio: datetime,
    ventanas: list[Intervalo],
    ocupaciones: list[Intervalo],
    duracion_min: int,
    buffer_antes_min: int,
    buffer_despues_min: int,
    reglas: ReglasCupo,
    ahora: datetime,
) -> bool:
    """¿Es `inicio` un cupo agendable ahora mismo? (misma regla que `calcular_slots`, para un instante).

    Lo usa `validar_y_agendar` para revalidar el cupo dentro de la sección crítica (bajo lock) antes
    de insertar: confirma que sigue dentro de una ventana, respeta las reglas y no choca capacidad.
    """
    return inicio in set(
        calcular_slots(
            ventanas=ventanas,
            ocupaciones=ocupaciones,
            duracion_min=duracion_min,
            buffer_antes_min=buffer_antes_min,
            buffer_despues_min=buffer_despues_min,
            reglas=reglas,
            ahora=ahora,
        )
    )
