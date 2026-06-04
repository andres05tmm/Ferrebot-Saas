"""Abstracción de proveedor LLM (ADR 0005 decisión b).

Cambiar de IA es configuración, no reescritura: el resto del sistema (despachador, herramientas)
depende solo del Protocol `LLMProvider` y de los tipos canónicos, jamás del SDK de un vendor.
"""
