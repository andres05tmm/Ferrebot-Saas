"""Prompt del bot de operación por RUBRO (config_empresa.rubro): la persona deja de estar
hardcodeada a ferretería. Contrato clave: `rubro=None` → prompt ferretero EXACTO de siempre
(Punto Rojo y todo tenant sin rubro configurado no cambian ni un byte).
"""
from datetime import date

from ai.turno import construir_system_prompt

_HOY = date(2026, 7, 1)


def test_sin_rubro_prompt_ferretero_exacto():
    # Snapshot del contrato: intro ferretera + TODAS las reglas de dominio ferretero presentes.
    prompt = construir_system_prompt({}, hoy=_HOY)
    assert prompt.startswith("Eres el asistente de ventas de una ferretería.")
    for marca in ("thinner", "esmalte blanco", "Lija", "esmeril", "Vinilos y cuñetes",
                  "inventario en cero o negativo"):
        assert marca in prompt, marca
    assert "Fecha de hoy (Colombia): 2026-07-01." in prompt


def test_rubro_ferreteria_explicito_equivale_al_fallback():
    # Setearle "ferretería" a Punto Rojo debe producir EXACTAMENTE el mismo prompt que el fallback.
    assert construir_system_prompt({}, rubro="ferretería", hoy=_HOY) == construir_system_prompt({}, hoy=_HOY)
    assert construir_system_prompt({}, rubro="Ferreteria", hoy=_HOY) == construir_system_prompt({}, hoy=_HOY)


def test_rubro_peluqueria_sin_bloques_ferreteros():
    prompt = construir_system_prompt({}, rubro="peluquería", hoy=_HOY)
    assert prompt.startswith("Eres el asistente de operación de una peluquería.")
    for ferretero in ("lija", "esmeril", "vinilo", "cuñete", "thinner", "drywall", "galón"):
        assert ferretero not in prompt.lower(), ferretero
    # Las reglas transversales se conservan: fecha, herramientas, texto plano, no inventar precios.
    assert "Fecha de hoy (Colombia): 2026-07-01." in prompt
    assert "nunca inventes valores" in prompt
    assert "consultar_producto" in prompt
    assert "texto plano para Telegram" in prompt


def test_rubro_conserva_contexto_reciente():
    entidades = {"ultimo_producto": {"id": 7, "nombre": "Shampoo"}}
    prompt = construir_system_prompt(entidades, rubro="peluquería", hoy=_HOY)
    assert "Shampoo" in prompt
