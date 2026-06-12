"""Validación/esquema del pack Pedidos + horas HH:MM (ADR 0016). PURO: sin red ni BD.

Cubre: hora mal formada en config (esquema), datos de pedidos sin la feature activa, tarifa negativa y
zona duplicada (validación semántica) → error; y el caso feliz (pack_pedidos sobre su dependencia pos).
"""
from __future__ import annotations

import pytest

from tools.manifest import ErrorManifiesto, Manifiesto, validar


def _manifiesto_pedidos(**packs_pedidos) -> dict:
    """Manifiesto mínimo con pos + pack_pedidos activos y una sección packs.pedidos."""
    return {
        "version": 1,
        "identidad": {"slug": "rest", "nombre": "Rest", "nit": "900-1"},
        "plan": {"nombre": "Agente", "features": ["pos", "pack_pedidos", "canal_whatsapp"]},
        "packs": {"pedidos": {"config": {}, "zonas": [], **packs_pedidos}},
        "canal": {"whatsapp": {"phone_number_id": "123"}},
    }


def test_pedidos_caso_feliz_valida():
    datos = _manifiesto_pedidos(
        config={"hora_apertura": "11:00", "hora_cierre": "22:00", "minimo_pedido": 20000},
        zonas=[{"nombre": "Centro", "tarifa": 3000}],
    )
    validar(Manifiesto.model_validate(datos))  # no lanza


def test_hora_mal_formada_es_error_de_esquema():
    datos = _manifiesto_pedidos(config={"hora_apertura": "25:00"})
    with pytest.raises(Exception, match="hora mal formada"):  # pydantic.ValidationError
        Manifiesto.model_validate(datos)


def test_pedidos_sin_feature_activa_falla():
    datos = _manifiesto_pedidos(zonas=[{"nombre": "Centro", "tarifa": 3000}])
    datos["plan"]["features"] = ["pos", "canal_whatsapp"]  # quita pack_pedidos
    with pytest.raises(ErrorManifiesto, match="pack_pedidos no está activa"):
        validar(Manifiesto.model_validate(datos))


def test_pedidos_sin_dependencia_pos_falla():
    datos = _manifiesto_pedidos(zonas=[{"nombre": "Centro", "tarifa": 3000}])
    datos["plan"]["features"] = ["pack_pedidos", "canal_whatsapp"]  # quita pos (dependencia)
    with pytest.raises(ErrorManifiesto, match="dependencia no satisfecha"):
        validar(Manifiesto.model_validate(datos))


def test_tarifa_negativa_falla():
    datos = _manifiesto_pedidos(zonas=[{"nombre": "Centro", "tarifa": -1}])
    with pytest.raises(ErrorManifiesto, match="tarifa debe ser >= 0"):
        validar(Manifiesto.model_validate(datos))


def test_zona_duplicada_falla():
    datos = _manifiesto_pedidos(
        zonas=[{"nombre": "Centro", "tarifa": 3000}, {"nombre": "  centro ", "tarifa": 4000}]
    )
    with pytest.raises(ErrorManifiesto, match="zona de domicilio duplicada"):
        validar(Manifiesto.model_validate(datos))


def test_checkin_checkout_default_y_formato():
    # Defaults espejan el server_default del esquema; una hora mal formada es error de esquema.
    datos = {
        "version": 1,
        "identidad": {"slug": "hotel", "nombre": "Hotel", "nit": "900-2"},
        "plan": {"nombre": "Agente", "features": ["pack_agenda", "pack_reservas", "canal_whatsapp"]},
        "packs": {"agenda": {"config": {"checkin_hora": "14:00", "checkout_hora": "11:00"}}},
        "canal": {"whatsapp": {"phone_number_id": "123"}},
    }
    m = Manifiesto.model_validate(datos)
    assert m.packs.agenda.config.checkin_hora == "14:00"
    validar(m)  # pack_reservas sobre pack_agenda: no lanza

    datos["packs"]["agenda"]["config"]["checkin_hora"] = "9:00"  # falta el cero inicial
    with pytest.raises(Exception, match="hora mal formada"):
        Manifiesto.model_validate(datos)
