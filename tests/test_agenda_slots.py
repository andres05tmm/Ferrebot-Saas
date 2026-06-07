"""Unit de la lógica PURA de cupos del pack Agenda/Citas (`modules/agenda/slots.py`), sin BD.

Cubre granularidad + duración + buffers, anticipación mínima, ventana máxima, resta de
ocupaciones (citas/bloqueos), capacidad_por_slot y la zona horaria Colombia. Todo determinista:
se fija `ahora` y se pasan ventanas/ocupaciones a mano.
"""
from datetime import date, datetime, time, timedelta

from core.config.timezone import COLOMBIA_TZ
from modules.agenda.slots import (
    HorarioSemanal,
    Intervalo,
    ReglasCupo,
    calcular_slots,
    cupo_disponible,
    expandir_ventanas,
)

LUNES = date(2026, 6, 8)  # weekday() == 0 (lunes)
# `ahora` muy anterior: la anticipación mínima y la ventana máxima no estorban salvo donde se prueban.
AHORA_LEJANO = datetime(2026, 6, 1, 0, 0, tzinfo=COLOMBIA_TZ)


def _co(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), tzinfo=COLOMBIA_TZ)


def _ventana(d: date, h0: int, h1: int) -> Intervalo:
    return Intervalo(_co(d, h0), _co(d, h1))


# --- granularidad + duración ------------------------------------------------
def test_rejilla_por_intervalo_y_duracion():
    """Ventana 9–12, duración 30, paso 15 → 9:00…11:30 (el último cupo cabe entero)."""
    slots = calcular_slots(
        ventanas=[_ventana(LUNES, 9, 12)],
        ocupaciones=[],
        duracion_min=30,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=ReglasCupo(intervalo_slots_min=15, anticipacion_minima_min=0),
        ahora=AHORA_LEJANO,
    )
    assert slots[0] == _co(LUNES, 9, 0)
    assert slots[-1] == _co(LUNES, 11, 30)  # 11:30 + 30 = 12:00, justo cabe
    assert _co(LUNES, 11, 45) not in slots   # se saldría de la ventana
    assert len(slots) == 11


# --- buffers ----------------------------------------------------------------
def test_buffers_recortan_la_ventana():
    """Ventana 9–10, duración 30, buffers 15/15 → solo 9:15 (huella [9:00, 10:00])."""
    slots = calcular_slots(
        ventanas=[_ventana(LUNES, 9, 10)],
        ocupaciones=[],
        duracion_min=30,
        buffer_antes_min=15,
        buffer_despues_min=15,
        reglas=ReglasCupo(intervalo_slots_min=15, anticipacion_minima_min=0),
        ahora=AHORA_LEJANO,
    )
    assert slots == [_co(LUNES, 9, 15)]


# --- anticipación mínima ----------------------------------------------------
def test_anticipacion_minima_descarta_cupos_cercanos():
    ahora = _co(LUNES, 9, 0)
    slots = calcular_slots(
        ventanas=[_ventana(LUNES, 9, 13)],
        ocupaciones=[],
        duracion_min=30,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=ReglasCupo(intervalo_slots_min=30, anticipacion_minima_min=120),
        ahora=ahora,
    )
    assert _co(LUNES, 10, 30) not in slots  # a 90 min: muy pronto
    assert slots[0] == _co(LUNES, 11, 0)    # a 120 min: primer cupo válido


# --- ventana máxima ---------------------------------------------------------
def test_ventana_maxima_descarta_cupos_lejanos():
    ahora = _co(LUNES, 8, 0)
    reglas = ReglasCupo(intervalo_slots_min=60, anticipacion_minima_min=0, ventana_maxima_dias=2)
    manana = LUNES + timedelta(days=1)
    lejos = LUNES + timedelta(days=5)
    slots = calcular_slots(
        ventanas=[_ventana(manana, 9, 11), _ventana(lejos, 9, 11)],
        ocupaciones=[],
        duracion_min=60,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=reglas,
        ahora=ahora,
    )
    assert all(s.date() == manana for s in slots)  # nada del día +5 (fuera de ventana)
    assert slots


# --- resta de ocupaciones (citas/bloqueos) ----------------------------------
def test_resta_ocupacion_que_solapa():
    """Cita/bloqueo [9:30, 9:45] tumba el cupo de 9:00 (huella [9,10]) pero no el de 10:00."""
    slots = calcular_slots(
        ventanas=[_ventana(LUNES, 9, 11)],
        ocupaciones=[Intervalo(_co(LUNES, 9, 30), _co(LUNES, 9, 45))],
        duracion_min=60,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=ReglasCupo(intervalo_slots_min=60, anticipacion_minima_min=0),
        ahora=AHORA_LEJANO,
    )
    assert slots == [_co(LUNES, 10, 0)]


def test_ocupacion_que_solo_toca_el_borde_no_bloquea():
    """[10:00, 10:30] toca el fin de la huella [9,10] pero no la solapa (semiabierto)."""
    slots = calcular_slots(
        ventanas=[_ventana(LUNES, 9, 11)],
        ocupaciones=[Intervalo(_co(LUNES, 10, 0), _co(LUNES, 10, 30))],
        duracion_min=60,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=ReglasCupo(intervalo_slots_min=60, anticipacion_minima_min=0),
        ahora=AHORA_LEJANO,
    )
    assert _co(LUNES, 9, 0) in slots


# --- capacidad_por_slot -----------------------------------------------------
def test_capacidad_por_slot_permite_concurrentes():
    """capacidad=2: un solo solape no tumba el cupo; dos sí (clase llena)."""
    base = dict(
        ventanas=[_ventana(LUNES, 9, 10)],
        duracion_min=60,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=ReglasCupo(intervalo_slots_min=60, anticipacion_minima_min=0, capacidad_por_slot=2),
        ahora=AHORA_LEJANO,
    )
    una = Intervalo(_co(LUNES, 9, 0), _co(LUNES, 9, 30))
    assert calcular_slots(ocupaciones=[una], **base) == [_co(LUNES, 9, 0)]
    assert calcular_slots(ocupaciones=[una, una], **base) == []


# --- zona horaria Colombia --------------------------------------------------
def test_expandir_ventanas_zona_colombia_y_dia_correcto():
    horarios = [HorarioSemanal(dia_semana=0, hora_inicio=time(9), hora_fin=time(12))]
    ventanas = expandir_ventanas(horarios, LUNES, LUNES)
    assert len(ventanas) == 1
    v = ventanas[0]
    assert v.inicio == _co(LUNES, 9, 0)
    assert v.inicio.utcoffset() == timedelta(hours=-5)  # Colombia UTC-5, sin DST
    assert v.inicio.tzinfo is COLOMBIA_TZ


def test_expandir_ventanas_solo_dias_que_coinciden_y_varias_franjas():
    horarios = [
        HorarioSemanal(0, time(8), time(12)),   # lunes mañana
        HorarioSemanal(0, time(14), time(18)),  # lunes tarde
        HorarioSemanal(2, time(9), time(13)),   # miércoles
        HorarioSemanal(5, time(9), time(9)),    # franja inválida (inicio==fin): se ignora
    ]
    ventanas = expandir_ventanas(horarios, LUNES, LUNES + timedelta(days=6))
    dias = sorted({v.inicio.date() for v in ventanas})
    assert dias == [LUNES, LUNES + timedelta(days=2)]  # solo lunes y miércoles
    lunes = [v for v in ventanas if v.inicio.date() == LUNES]
    assert len(lunes) == 2  # mañana + tarde


# --- cupo_disponible (revalidación puntual usada al agendar) -----------------
def test_cupo_disponible_coincide_con_calcular_slots():
    kw = dict(
        ventanas=[_ventana(LUNES, 9, 11)],
        ocupaciones=[Intervalo(_co(LUNES, 9, 30), _co(LUNES, 9, 45))],
        duracion_min=60,
        buffer_antes_min=0,
        buffer_despues_min=0,
        reglas=ReglasCupo(intervalo_slots_min=60, anticipacion_minima_min=0),
        ahora=AHORA_LEJANO,
    )
    assert cupo_disponible(inicio=_co(LUNES, 10, 0), **kw) is True
    assert cupo_disponible(inicio=_co(LUNES, 9, 0), **kw) is False   # ocupado
    assert cupo_disponible(inicio=_co(LUNES, 9, 7), **kw) is False   # fuera de rejilla
