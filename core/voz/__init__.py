"""Voz del bot: transcripción (Whisper) + filtros de ruido/alucinación (ai-tools.md §7).

El audio se enchufa ANTES del pipeline del turno: descargar nota de voz → transcribir → filtrar
silencio/alucinación → mismo flujo con el texto transcrito. Los adaptadores reales (httpx) se
cablean en el composition root; aquí viven los puertos, los tipos y la lógica pura de filtros.
"""
