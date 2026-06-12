"""Manifiesto: `branding.preset` validado contra el registro de presets (plan §5.2, punto 4).

PURO (sin BD): un preset inválido se RECHAZA al parsear con un mensaje claro; los 4 manifiestos demo
declaran su preset por vertical; un preset ausente es válido (→ default melquiadez al resolver). Cubre
también que `color_primario` ya no se autollena (None permitido: el primario lo pone el preset).
"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.manifest.loader import cargar_manifiesto
from tools.manifest.schema import Branding

_ONBOARDING = Path(__file__).parents[1] / "tools" / "onboarding"
_DEMOS = {
    "clinica-demo": "aurora",
    "barberia-demo": "navaja",
    "restaurante-demo": "brasa",
    "hotel-demo": "brisa",
}


def test_preset_invalido_se_rechaza_con_mensaje_claro():
    with pytest.raises(ValidationError) as exc:
        Branding(preset="zapateria")
    msg = str(exc.value)
    assert "zapateria" in msg and "preset" in msg


def test_preset_valido_aceptado():
    assert Branding(preset="navaja").preset == "navaja"
    assert Branding(preset="melquiadez").preset == "melquiadez"


def test_preset_ausente_es_valido_y_color_no_se_autollena():
    b = Branding()
    assert b.preset is None
    assert b.color_primario is None          # ya no nace #C8200E: el primario lo pone el preset


@pytest.mark.parametrize("slug,preset", sorted(_DEMOS.items()))
def test_manifiestos_demo_declaran_su_preset(slug, preset):
    m = cargar_manifiesto(_ONBOARDING / f"{slug}.manifest.example.yaml")
    assert m.branding.preset == preset
    assert m.branding.color_primario is None  # el preset manda el primario, no un color suelto
