"""Bucle del agente para un turno (ADR 0005). Reemplaza el `ai/__init__.py` monolítico de FerreBot.

Un turno, determinista y acotado (checkpoint fase 5, decisión B2):

  - **Tope duro: 2 generaciones de modelo y 1 herramienta mutante por turno.**
  - El modelo elige UNA herramienta (se toma el primer `tool_call`); el despachador la ejecuta
    con sus rieles/RBAC/idempotencia.
  - **Respuesta NL híbrida:**
      · Éxito (`Resultado`) → se usa el `resumen` del envelope. **Cero llamada extra al modelo.**
      · Riel `Preguntar`/`Confirmar` → su mensaje va **directo** al usuario, sin re-promptear
        (el riel ya produjo texto en español; `producto_ambiguo` lista los candidatos).
      · `ErrorTool` recuperable del servicio → **2ª generación** re-prompteando con el tool_result
        para que el modelo ajuste o repregunte. Tras ella NO se ejecuta otra herramienta.
      · `ErrorTool` no recuperable (permiso/capacidad/…) → mensaje directo, sin re-prompt.

El transporte (Telegram), el contexto RAG y la voz son otros entregables; aquí el loop recibe el
texto del turno, el proveedor ya resuelto y el despachador como `Ejecutor`.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.rieles import Confirmar, Preguntar
from core.llm.base import Message, ToolCall, ToolSpec
from core.llm.factory import LLMResuelto
from core.logging import get_logger

log = get_logger("ai.agent")

# Topes del turno (checkpoint B2).
MAX_GENERACIONES = 2
MAX_TOOLS = 1

# Texto de respaldo si el modelo no devuelve nada utilizable.
SIN_RESPUESTA = "No entendí. ¿Puedes repetirlo de otra forma?"

# Mensajes al usuario para errores NO recuperables (no se re-promptea al modelo).
_MENSAJES_ERROR = {
    "permiso_denegado": "No tienes permiso para esa operación.",
    "capacidad_no_habilitada": "Esa función no está habilitada para tu empresa.",
    "idempotencia_conflicto": "Esa operación ya se registró antes con datos distintos.",
    "error_interno": "Ocurrió un error inesperado. Vuelve a intentarlo.",
}

# Lo que devuelve `Ejecutor.ejecutar` (espeja `ai.dispatcher.Respuesta`).
RespuestaEjecutor = Resultado | ErrorTool | Preguntar | Confirmar


@dataclass(frozen=True, slots=True)
class RespuestaAgente:
    """Salida de un turno: el texto para el usuario + metadatos para observabilidad."""

    texto: str
    ruta: str                       # "texto" | "tool" | "riel" | "error"
    evento: str | None = None       # evento SSE del servicio (si hubo mutación)
    idempotente: str | None = None  # "aplicada" | "duplicada" | None
    generaciones: int = 0           # cuántas veces se llamó al modelo
    tool: str | None = None         # herramienta ejecutada (o None)
    # ToolCall a confirmar (solo en la rama `Confirmar`); el handler lo guarda para el re-despacho.
    confirmacion_pendiente: ToolCall | None = None
    # Payload del `Resultado` de la herramienta (solo en la rama de éxito): fuente del writer de
    # `memoria_entidades` (ADR 0024). None en las demás rutas (texto/riel/error).
    data: dict | None = None


class Ejecutor(Protocol):
    """Lo que el loop necesita del despachador (lo satisface `ai.dispatcher.Dispatcher`)."""

    def exponer_catalogo(self, ctx: Contexto) -> list[ToolSpec]: ...
    async def ejecutar(self, tool_call: ToolCall, ctx: Contexto, recursos) -> RespuestaEjecutor: ...


async def ejecutar_turno(
    *,
    texto: str,
    ctx: Contexto,
    ejecutor: Ejecutor,
    recursos,
    proveedor: LLMResuelto,
    historial: Sequence[Message] = (),
    system: str | None = None,
) -> RespuestaAgente:
    """Corre un turno completo respetando los topes y la política de respuesta NL híbrida."""
    tools = ejecutor.exponer_catalogo(ctx)
    mensajes: list[Message] = [*historial, Message(role="user", content=texto)]

    # 1ª generación: el modelo responde texto o pide una herramienta.
    resp = await proveedor.provider.generate(
        messages=mensajes, tools=tools, model=proveedor.model, system=system
    )
    if not resp.tool_calls:
        return _final(RespuestaAgente(texto=resp.text or SIN_RESPUESTA, ruta="texto", generaciones=1))

    # Tope duro: una sola herramienta por turno (el primer tool_call).
    call = resp.tool_calls[0]
    resultado = await ejecutor.ejecutar(call, ctx, recursos)

    if isinstance(resultado, Preguntar):
        # Corte de riel → directo al usuario, sin re-promptear (el mensaje ya es autosuficiente).
        return _final(RespuestaAgente(
            texto=texto_de_respuesta(resultado), ruta="riel", generaciones=1, tool=call.name))
    if isinstance(resultado, Confirmar):
        # Riel de confirmación → se guarda el tool_call para re-despacharlo cuando el usuario diga "sí".
        return _final(RespuestaAgente(
            texto=texto_de_respuesta(resultado), ruta="riel", generaciones=1, tool=call.name,
            confirmacion_pendiente=call,
        ))
    if isinstance(resultado, Resultado):
        # Éxito → resumen del envelope; cero llamada extra al modelo.
        return _final(RespuestaAgente(
            texto=texto_de_respuesta(resultado), ruta="tool", evento=resultado.evento,
            idempotente=resultado.idempotente, generaciones=1, tool=call.name,
            data=resultado.data,
        ))

    # ErrorTool no recuperable → mensaje directo, sin 2ª generación.
    if not resultado.recuperable:
        return _final(RespuestaAgente(
            texto=texto_de_respuesta(resultado), ruta="error", generaciones=1, tool=call.name))

    # ErrorTool recuperable → 2ª generación con la tripleta tool_use→tool_result bien formada.
    mensajes = [
        *mensajes,
        Message(role="assistant", content=resp.text or "", tool_calls=[call]),
        Message(role="tool", content=_envelope_json(resultado), tool_call_id=call.id, name=call.name),
    ]
    resp2 = await proveedor.provider.generate(
        messages=mensajes, tools=tools, model=proveedor.model, system=system
    )
    # Tope: tras la 2ª generación NO se ejecuta otra herramienta; se devuelve su texto.
    return _final(RespuestaAgente(texto=resp2.text or SIN_RESPUESTA, ruta="texto", generaciones=2, tool=call.name))


def texto_de_respuesta(resultado: RespuestaEjecutor) -> str:
    """Texto para el usuario de un resultado del despachador (mismo mapeo que usa el loop).

    Reusado por el re-despacho de confirmación (CR-2) para no duplicar el mapeo riel/error.
    """
    if isinstance(resultado, Preguntar):
        return resultado.mensaje
    if isinstance(resultado, Confirmar):
        return resultado.resumen
    if isinstance(resultado, Resultado):
        return resultado.resumen
    return _mensaje_error(resultado)


def _envelope_json(error: ErrorTool) -> str:
    """Serializa el error como el envelope que ve el modelo en el tool_result (ai-tools.md §3)."""
    return json.dumps(
        {"ok": False, "error": error.error, "detail": error.detail, "recuperable": error.recuperable},
        ensure_ascii=False,
    )


def _mensaje_error(error: ErrorTool) -> str:
    return _MENSAJES_ERROR.get(error.error) or error.detail or "No pude completar la operación."


def _final(respuesta: RespuestaAgente) -> RespuestaAgente:
    """Punto único de salida: logging estructurado del turno (request_id/tenant_id ya en contexto)."""
    log.info(
        "agente_turno", ruta=respuesta.ruta, tool=respuesta.tool,
        generaciones=respuesta.generaciones, evento=respuesta.evento,
    )
    return respuesta
