"""Recuperación de conocimiento DETRÁS DE UN PUERTO (`Recuperador`).

El servicio/agente solo pide `recuperar(pregunta)` y recibe entradas; NO sabe cómo se recuperan. Así se
puede cambiar la implementación a embeddings/pgvector (RAG real, v2) sin tocar el agente ni el servicio.

v1 (`RecuperadorKeyword`): recuperación simple por palabras clave sobre `titulo` + `contenido`
(normalizando acentos/mayúsculas), con un respaldo "si son pocas, devuélvelas todas". Suficiente para
el piloto; sin dependencias de modelo.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from modules.faq.models import Conocimiento
from modules.faq.repository import ConocimientoRepo

# Cuántas entradas recuperar como máximo, y el puntaje mínimo de palabras clave para considerarlas.
_LIMITE_DEFECTO = 5
_UMBRAL = 1


def _normalizar(texto: str) -> str:
    """Minúsculas sin acentos (NFKD): hace el match robusto a tildes/mayúsculas ('Ubicación'→'ubicacion')."""
    desc = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in desc if not unicodedata.combining(c))


def _tokens(texto: str) -> set[str]:
    """Conjunto de palabras (≥3 letras descartan ruido como 'de'/'la' implícitamente al puntuar)."""
    return set(re.findall(r"\w+", _normalizar(texto)))


def _puntaje(pregunta_tokens: set[str], entrada: Conocimiento) -> int:
    """Solapamiento de palabras de la pregunta con `titulo` + `contenido` (el título pesa doble)."""
    titulo = _tokens(entrada.titulo)
    cuerpo = _tokens(entrada.contenido)
    return 2 * len(pregunta_tokens & titulo) + len(pregunta_tokens & cuerpo)


class Recuperador(Protocol):
    """Puerto de recuperación de conocimiento. v2 (embeddings/RAG) implementa la misma firma."""

    async def recuperar(self, pregunta: str, *, limite: int = _LIMITE_DEFECTO) -> list[Conocimiento]:
        """Entradas relevantes para `pregunta` (vacío = no hay información suficiente)."""
        ...


class RecuperadorKeyword:
    """v1: recupera por palabras clave sobre las entradas activas del tenant. Lee vía el repo (puerto)."""

    def __init__(self, repo: ConocimientoRepo) -> None:
        self._repo = repo

    async def recuperar(
        self, pregunta: str, *, limite: int = _LIMITE_DEFECTO
    ) -> list[Conocimiento]:
        entradas = await self._repo.listar(solo_activas=True)
        if len(entradas) <= limite:
            # Pocas (o ninguna): dáselas todas al agente; si son cero, devuelve [] (señal de "sin info").
            return entradas
        pregunta_tokens = _tokens(pregunta)
        rankeadas = sorted(entradas, key=lambda e: _puntaje(pregunta_tokens, e), reverse=True)
        relevantes = [e for e in rankeadas if _puntaje(pregunta_tokens, e) >= _UMBRAL]
        return relevantes[:limite]
