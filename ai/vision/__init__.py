"""Capa de Visión del agente (Fase 6 — Bot PIM).

Extracción estructurada desde imágenes (foto → JSON validado). Hoy: comprobantes de pago de
Bancolombia (`recibo`). No persiste nada; solo extrae y valida.
"""
from ai.vision.recibo import UMBRAL_REVISION, ReciboExtraido, extraer_recibo

__all__ = ["UMBRAL_REVISION", "ReciboExtraido", "extraer_recibo"]
