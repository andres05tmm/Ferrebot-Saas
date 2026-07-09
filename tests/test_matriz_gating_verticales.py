"""Matriz de gating por vertical: ningún tenant nuevo hereda capacidades de otra familia.

Regresión que ocurrió 3 veces (demos, PIM, gating por familia): un tenant provisionado desde
plantilla terminaba viendo tabs de retail (Punto Rojo) que no eran de su vertical. Este test
es la red mecánica: por cada plantilla de manifiesto (`tools/onboarding/*.manifest.example.yaml`)
verifica qué capacidades DEBE tener y cuáles NO PUEDE tener tras expandir meta-packs, que sus
dependencias validan, y que toda plantilla nueva quede cubierta por la matriz.

PURO (sin BD): manifiesto → plan.features → capacidades_completas (lo mismo que consume
GET /config y de ahí el gating del dashboard, dashboard/src/lib/features.jsx).
"""
from pathlib import Path

import pytest

from core.tenancy.catalogo import capacidades_completas, validar_dependencias
from tools.manifest.loader import cargar_manifiesto

_ONBOARDING = Path(__file__).parents[1] / "tools" / "onboarding"

_RETAIL = {"pos", "ventas", "caja", "inventario"}
_CONSTRUCCION = {"construccion", "obras", "maquinaria", "herramientas", "cotizaciones_aiu"}

# vertical → (capacidades que DEBE tener, capacidades que NO PUEDE tener).
# Clave = stem del archivo `<clave>.manifest.example.yaml`.
MATRIZ: dict[str, tuple[set[str], set[str]]] = {
    # Atención a cliente puro: sin retail, sin obra.
    "clinica-demo": ({"pack_agenda"}, _RETAIL | _CONSTRUCCION),
    "hotel-demo": ({"pack_agenda", "pack_reservas"}, _RETAIL | _CONSTRUCCION),
    # Carril contable de servicios (ADR 0021): caja+ventas SIN inventario/kárdex ni pos.
    "barberia-demo": ({"pack_agenda", "caja", "ventas"}, {"pos", "inventario"} | _CONSTRUCCION),
    "peluqueria-demo": ({"pack_agenda", "caja", "ventas"}, {"pos", "inventario"} | _CONSTRUCCION),
    # Retail/POS: todo el retail, nada de obra ni agenda.
    "ferreteria-demo": ({"ventas", "caja", "inventario"}, _CONSTRUCCION | {"pack_agenda"}),
    "restaurante-demo": ({"ventas", "caja", "inventario", "pack_pedidos"}, _CONSTRUCCION | {"pack_agenda"}),
    # Construcción (PIM): obra + retail explícito (compras a obra), sin agenda.
    "construcciones-pim": (
        {"obras", "maquinaria", "ventas", "caja", "inventario"},
        {"pack_agenda", "pack_reservas"},
    ),
}


def _capacidades(clave: str) -> frozenset[str]:
    m = cargar_manifiesto(_ONBOARDING / f"{clave}.manifest.example.yaml")
    return capacidades_completas(frozenset(m.plan.features))


def test_toda_plantilla_esta_en_la_matriz():
    """Una plantilla nueva sin fila en la matriz falla acá: el gating se decide al crearla, no a la tercera regresión."""
    plantillas = {p.name.removesuffix(".manifest.example.yaml") for p in _ONBOARDING.glob("*.manifest.example.yaml")}
    assert plantillas == set(MATRIZ), (
        f"plantillas sin fila en MATRIZ: {sorted(plantillas - set(MATRIZ))}; "
        f"filas sin plantilla: {sorted(set(MATRIZ) - plantillas)}"
    )


@pytest.mark.parametrize("clave", sorted(MATRIZ))
def test_matriz_gating(clave):
    debe, no_puede = MATRIZ[clave]
    caps = _capacidades(clave)
    assert debe <= caps, f"{clave}: faltan capacidades de su vertical: {sorted(debe - caps)}"
    fugas = no_puede & caps
    assert not fugas, f"{clave}: hereda capacidades de OTRA familia: {sorted(fugas)}"


@pytest.mark.parametrize("clave", sorted(MATRIZ))
def test_dependencias_de_plantilla_validan(clave):
    m = cargar_manifiesto(_ONBOARDING / f"{clave}.manifest.example.yaml")
    assert validar_dependencias(frozenset(m.plan.features)) == []


@pytest.mark.parametrize("clave", sorted(MATRIZ))
def test_sin_retail_declarado_no_aparece_inventario(clave):
    """La regla general anti-herencia: si la plantilla no pidió retail, la expansión no lo inyecta."""
    m = cargar_manifiesto(_ONBOARDING / f"{clave}.manifest.example.yaml")
    declaradas = set(m.plan.features)
    if "pos" not in declaradas and "inventario" not in declaradas:
        assert "inventario" not in _capacidades(clave)
