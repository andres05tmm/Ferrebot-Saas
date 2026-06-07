"""Confirmación hablada de mutaciones entre turnos (re-despacho determinista).

El despachador emite `Confirmar` (puro) y se relaya al usuario; el "sí" del siguiente turno NO lo
ve el modelo. Mecanismo: se guarda el `ToolCall` pendiente + su `idempotency_key` en un `ConfirmStore`
(Redis, TTL ~300s, clave por (tenant, chat)); al confirmar, el handler re-ejecuta ese `ToolCall`
por el dispatcher con `confirmado=True` y la MISMA key, sin volver a llamar al modelo.

Este módulo es dominio PURO (sin Redis): el tipo `Pendiente`, el puerto `ConfirmStore`, la
clasificación del texto (`es_afirmacion`/`es_negacion`) y la (de)serialización JSON —la parte
riesgosa, testeable sin red—. El adaptador Redis vive en `apps/bot/redis_stores.py`.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from core.llm.base import ToolCall

# Solo un "sí" claro mueve plata (decisión CR-2). Negación explícita = descartar + "cancelado".
_AFIRMACIONES = frozenset({
    "si", "sí", "dale", "confirmo", "confirmar", "ok", "okay", "listo", "hagale", "hágale",
    "correcto", "claro", "de once", "eso", "hazlo", "registralo", "regístralo",
})
_NEGACIONES = frozenset({"no", "cancela", "cancelar", "nada", "negativo"})


@dataclass(frozen=True, slots=True)
class Pendiente:
    """Mutación a la espera de confirmación: la herramienta a re-ejecutar + su clave de idempotencia."""

    tool_call: ToolCall
    idempotency_key: str


class ConfirmStore(Protocol):
    """Puerto del pendiente por (tenant, chat). Faked en tests; Redis en prod (TTL ~300s)."""

    async def guardar(
        self, tenant_id: int, chat_id: int, *, tool_call: ToolCall, idempotency_key: str
    ) -> None: ...
    async def obtener(self, tenant_id: int, chat_id: int) -> Pendiente | None: ...
    async def borrar(self, tenant_id: int, chat_id: int) -> None: ...


class VentaPendienteStore(Protocol):
    """Pendiente de método de pago por (tenant, chat): la venta lista a ejecutar salvo el `metodo_pago`.

    El bypass guarda el `ToolCall(registrar_venta)` SIN `metodo_pago` y muestra botones; el callback
    del botón completa el método y re-despacha. Mismo `Pendiente` (tool_call + idempotency_key) y el
    mismo patrón que `ConfirmStore`; la clave en Redis difiere (`venta_pendiente:{tenant}:{chat}`)."""

    async def guardar(
        self, tenant_id: int, chat_id: int, *, tool_call: ToolCall, idempotency_key: str
    ) -> None: ...
    async def obtener(self, tenant_id: int, chat_id: int) -> Pendiente | None: ...
    async def borrar(self, tenant_id: int, chat_id: int) -> None: ...


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, puntuación→espacio, espacios colapsados (para match exacto)."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in sin_tildes.lower())
    return " ".join(limpio.split())


def es_afirmacion(texto: str) -> bool:
    """True si el texto es un 'sí' claro (normalizado: minúsculas, sin tildes/puntuación)."""
    return _normalizar(texto) in _AFIRMACIONES


def es_negacion(texto: str) -> bool:
    """True si el texto es una negación explícita ('no'/'cancela'/'cancelar')."""
    return _normalizar(texto) in _NEGACIONES


def _json_default(obj: Any) -> str:
    """`default` de `json.dumps` SOLO para `Decimal` → str (las cantidades del ToolCall vienen como
    Decimal). No es un `default=str` ciego: cualquier otro tipo no serializable re-lanza `TypeError`
    para no enmascarar bugs. Al deserializar, la cantidad vuelve como str y el `args_model` (Pydantic)
    de la herramienta la coacciona a Decimal al ejecutar."""
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _serializar(pendiente: Pendiente) -> str:
    """Pendiente → JSON `{id, name, arguments, key}` (lo que se guarda en Redis)."""
    tc = pendiente.tool_call
    return json.dumps(
        {"id": tc.id, "name": tc.name, "arguments": tc.arguments, "key": pendiente.idempotency_key},
        ensure_ascii=False,
        default=_json_default,
    )


def _deserializar(dato: str) -> Pendiente:
    """JSON → Pendiente. Las cantidades vuelven como str; el `args_model` de la herramienta las
    coacciona a Decimal al ejecutar (round-trip por valor, no por tipo)."""
    d = json.loads(dato)
    tool_call = ToolCall(id=d["id"], name=d["name"], arguments=d.get("arguments") or {})
    return Pendiente(tool_call=tool_call, idempotency_key=d["key"])
