"""Presets de branding por vertical (plan §5.2): datos + resolución preset+overrides.

Módulo PURO (sin BD): cubre que cada preset declara el set completo de tokens, que la resolución
parte del preset (default `melquiadez` de plataforma) y que un `color_primario` explícito GANA sobre
el preset (Punto Rojo conserva su rojo). Estos invariantes los consume el control DB (`leer_branding`)
y GET /config.
"""
import pytest

from core.tenancy.branding_presets import (
    DEFAULT_PRESET,
    PRESETS,
    TOKEN_KEYS,
    es_preset_valido,
    resolver_branding,
)


def test_default_es_melquiadez():
    assert DEFAULT_PRESET == "melquiadez"
    assert "melquiadez" in PRESETS


def test_los_presets_existen():
    assert set(PRESETS) == {"aurora", "brasa", "navaja", "brisa", "lienzo", "obra", "melquiadez"}


@pytest.mark.parametrize("nombre", sorted(PRESETS))
def test_cada_preset_declara_todos_los_tokens(nombre):
    tokens = PRESETS[nombre].tokens()
    assert set(tokens) == set(TOKEN_KEYS)
    # Sin valores vacíos: cada token resuelve a algo aplicable como CSS var.
    assert all(isinstance(v, str) and v.strip() for v in tokens.values())


def test_resolver_sin_fila_usa_default_melquiadez():
    res = resolver_branding(None)
    assert res["preset"] == "melquiadez"
    assert res["tokens"]["primario"] == PRESETS["melquiadez"].tokens()["primario"]
    # Compat: color_primario plano sigue presente y espeja el primario resuelto.
    assert res["color_primario"] == res["tokens"]["primario"]


def test_resolver_preset_navaja_aplica_sus_tokens():
    res = resolver_branding({"preset": "navaja"})
    navaja = PRESETS["navaja"].tokens()
    assert res["preset"] == "navaja"
    assert res["tokens"]["superficie"] == navaja["superficie"]
    assert res["tokens"]["font_display"] == navaja["font_display"]
    assert res["tokens"]["primario"] == navaja["primario"]


def test_color_primario_explicito_gana_sobre_el_preset():
    # Punto Rojo: fila con preset implícito (None → melquiadez) pero color rojo explícito.
    res = resolver_branding({"preset": None, "color_primario": "#C8200E"})
    assert res["tokens"]["primario"] == "#C8200E"
    assert res["color_primario"] == "#C8200E"
    # El override del primario arrastra el hover (primario_up) para no romper el contraste.
    assert res["tokens"]["primario_up"] == "#C8200E"
    # El resto de tokens siguen siendo del preset base (no se inventan).
    assert res["tokens"]["superficie"] == PRESETS["melquiadez"].tokens()["superficie"]


def test_override_color_sobre_preset_navaja_conserva_resto_de_tokens():
    res = resolver_branding({"preset": "navaja", "color_primario": "#1C1A17"})
    navaja = PRESETS["navaja"].tokens()
    assert res["tokens"]["primario"] == "#1C1A17"          # gana el explícito
    assert res["tokens"]["card"] == navaja["card"]          # resto = navaja
    assert res["tokens"]["tinta"] == navaja["tinta"]


def test_preset_desconocido_cae_al_default_sin_reventar():
    res = resolver_branding({"preset": "zapateria"})
    assert res["preset"] == "melquiadez"                    # desconocido → default seguro
    assert res["tokens"]["primario"] == PRESETS["melquiadez"].tokens()["primario"]


def test_es_preset_valido():
    assert es_preset_valido("navaja")
    assert es_preset_valido("melquiadez")
    assert not es_preset_valido("zapateria")
    assert not es_preset_valido("")


def test_overrides_legacy_pasan_a_traves():
    res = resolver_branding({
        "preset": "aurora", "logo_url": "http://x/l.png",
        "nombre_comercial": "Clínica X", "dominio": "x.co",
    })
    assert res["logo_url"] == "http://x/l.png"
    assert res["nombre_comercial"] == "Clínica X"
    assert res["dominio"] == "x.co"
