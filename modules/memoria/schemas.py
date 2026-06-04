"""Contratos de datos de la memoria conversacional (sin lógica).

`MensajeGuardado` y `EntidadGuardada` son lo que el repositorio devuelve al servicio; el servicio
los traduce al vocabulario de la capa IA (`core.llm.base.Message`) y a dicts del prompt. Los tipos
de entidad codifican el alcance del scratch (decisión E4): un último cliente y un último producto
por chat, sin `chat_id` ni `UNIQUE` en la tabla base (el alcance lo da `clave = str(chat_id)`).
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MensajeGuardado:
    """Una fila de `conversaciones_bot` (columnas exactas que importan: rol + contenido)."""

    rol: str          # 'user' | 'assistant' (otros roles se ignoran al cargar historial)
    contenido: str


@dataclass(frozen=True, slots=True)
class EntidadGuardada:
    """Una fila de `memoria_entidades`: el tipo + su valor JSONB (round-trip)."""

    tipo: str         # 'ultimo_cliente' | 'ultimo_producto'
    valor: dict[str, Any]   # {"id": ..., "nombre": ...}
