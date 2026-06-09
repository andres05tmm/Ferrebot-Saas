"""Manifiesto de tenant — Fase 1: parsing + validación (ADR 0007). PURO: sin red ni BD.

Cubre: el ejemplo parsea y valida OK; feature inexistente, dependencia faltante, `presta` a servicio
inexistente, franja mal formada y tipo de recurso inválido → `ErrorManifiesto`. Las variantes inválidas
parten del ejemplo y mutan un solo campo (todo lo demás sigue siendo válido).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from tools.manifest import ErrorManifiesto, Manifiesto, cargar_manifiesto, validar

_EJEMPLO = Path(__file__).parents[1] / "tools" / "onboarding" / "clinica-demo.manifest.example.yaml"


def _datos_ejemplo() -> dict:
    """Dict crudo del ejemplo, para mutar un campo y rearmar el Manifiesto en los casos negativos."""
    return yaml.safe_load(_EJEMPLO.read_text(encoding="utf-8"))


def _manifiesto(datos: dict) -> Manifiesto:
    return Manifiesto.model_validate(datos)


# --- Caso feliz -----------------------------------------------------------

def test_ejemplo_parsea_y_valida_ok():
    manifiesto = cargar_manifiesto(_EJEMPLO)
    validar(manifiesto)  # no lanza

    assert manifiesto.identidad.slug == "clinica-demo"
    assert manifiesto.plan is not None and "pack_agenda" in manifiesto.plan.features
    # Pack agenda tipado 1:1.
    agenda = manifiesto.packs.agenda
    assert agenda is not None
    assert len(agenda.servicios) == 3
    assert len(agenda.recursos) == 2
    assert agenda.config.modo_confirmacion == "manual"
    assert agenda.config.recordatorios_horas == [24, 2]
    assert agenda.recursos[0].disponibilidad[0].franjas == ["08:00-12:00", "14:00-18:00"]
    # Pack FAQ y canal.
    assert manifiesto.packs.faq is not None and len(manifiesto.packs.faq.entradas) == 4
    assert manifiesto.canal.whatsapp is not None
    assert manifiesto.canal.whatsapp.phone_number_id == "1176767388843502"


def test_loader_acepta_json(tmp_path: Path):
    # yaml.safe_load también parsea JSON (JSON ⊂ YAML): un manifiesto mínimo en JSON valida.
    destino = tmp_path / "minimo.json"
    destino.write_text(
        '{"version": 1, "identidad": {"slug": "min", "nombre": "Min", "nit": "900-1"}}',
        encoding="utf-8",
    )
    manifiesto = cargar_manifiesto(destino)
    validar(manifiesto)  # solo-núcleo, sin packs → válido
    assert manifiesto.identidad.slug == "min"
    assert manifiesto.packs.agenda is None


def test_nit_ausente_es_error_de_esquema():
    # empresas.nit es NOT NULL + UNIQUE: un NIT ausente debe fallar limpio en validación de esquema.
    datos = _datos_ejemplo()
    del datos["identidad"]["nit"]
    with pytest.raises(Exception):  # pydantic.ValidationError
        _manifiesto(datos)


# --- Casos negativos (falla cerrado) --------------------------------------

def test_feature_inexistente_falla():
    datos = _datos_ejemplo()
    datos["plan"]["features"].append("no_existe")
    with pytest.raises(ErrorManifiesto, match="feature desconocida: 'no_existe'"):
        validar(_manifiesto(datos))


def test_dependencia_faltante_falla():
    # libro_iva requiere facturacion_electronica o compras_fiscal; sin ellos → error.
    datos = _datos_ejemplo()
    datos["plan"]["features"] = ["libro_iva"]
    with pytest.raises(ErrorManifiesto, match="dependencia no satisfecha"):
        validar(_manifiesto(datos))


def test_presta_a_servicio_inexistente_falla():
    datos = _datos_ejemplo()
    datos["packs"]["agenda"]["recursos"][0]["presta"] = ["Servicio Fantasma"]
    with pytest.raises(ErrorManifiesto, match="Servicio Fantasma.*no está declarado"):
        validar(_manifiesto(datos))


def test_franja_mal_formada_falla():
    datos = _datos_ejemplo()
    datos["packs"]["agenda"]["recursos"][0]["disponibilidad"][0]["franjas"] = ["8-12"]
    with pytest.raises(ErrorManifiesto, match="franja mal formada"):
        validar(_manifiesto(datos))


def test_tipo_de_recurso_invalido_falla():
    datos = _datos_ejemplo()
    datos["packs"]["agenda"]["recursos"][0]["tipo"] = "robot"
    with pytest.raises(ErrorManifiesto, match="tipo inválido 'robot'"):
        validar(_manifiesto(datos))


def test_dia_fuera_de_rango_falla():
    datos = _datos_ejemplo()
    datos["packs"]["agenda"]["recursos"][0]["disponibilidad"][0]["dias"] = [0, 7]
    with pytest.raises(ErrorManifiesto, match="día fuera de rango 7"):
        validar(_manifiesto(datos))


def test_agenda_con_datos_sin_pack_agenda_falla():
    # Datos de agenda declarados pero la feature no está en el set efectivo → incoherencia.
    datos = _datos_ejemplo()
    datos["plan"]["features"] = ["pack_faq", "canal_whatsapp"]  # quita pack_agenda
    with pytest.raises(ErrorManifiesto, match="pack_agenda no está activa"):
        validar(_manifiesto(datos))


def test_faq_con_datos_sin_pack_faq_falla():
    datos = _datos_ejemplo()
    datos["plan"]["features"] = ["pack_agenda", "canal_whatsapp"]  # quita pack_faq
    with pytest.raises(ErrorManifiesto, match="pack_faq no está activa"):
        validar(_manifiesto(datos))


def test_canal_sin_canal_whatsapp_falla():
    datos = _datos_ejemplo()
    datos["plan"]["features"] = ["pack_agenda", "pack_faq"]  # quita canal_whatsapp
    with pytest.raises(ErrorManifiesto, match="canal_whatsapp no está activa"):
        validar(_manifiesto(datos))


def test_override_puede_activar_el_flag_de_un_pack():
    # La coherencia mira el set EFECTIVO: un override que enciende el flag basta (no hace falta en plan).
    datos = _datos_ejemplo()
    datos["plan"]["features"] = ["pack_faq", "canal_whatsapp"]  # sin pack_agenda en el plan
    datos["features_override"] = {"pack_agenda": True}          # … pero activado por override
    validar(_manifiesto(datos))  # no lanza


def test_reune_varios_errores_en_un_solo_mensaje():
    datos = _datos_ejemplo()
    datos["plan"]["features"].append("no_existe")
    datos["packs"]["agenda"]["recursos"][0]["tipo"] = "robot"
    with pytest.raises(ErrorManifiesto) as exc:
        validar(_manifiesto(datos))
    mensaje = str(exc.value)
    assert "no_existe" in mensaje and "robot" in mensaje
    assert "2 error(es)" in mensaje


def test_campo_no_modelado_es_error_de_esquema():
    # extra="forbid": un typo en una clave (falla cerrado) lo atrapa el parseo, no pasa inadvertido.
    datos = copy.deepcopy(_datos_ejemplo())
    datos["identidadd"] = {}  # typo
    with pytest.raises(Exception):  # pydantic.ValidationError
        _manifiesto(datos)
