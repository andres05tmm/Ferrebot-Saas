"""Herramienta TRANSVERSAL del runtime de cara al cliente: `responder_faq` (conocimiento del negocio).

No pertenece a un pack de dominio: cualquier agente (agenda, catálogo…) consulta el conocimiento del
negocio (ubicación, horarios, precios, formas de pago, parqueo, políticas…) para resolver dudas
generales. A diferencia de `escalar_humano` (núcleo), está gateada por el flag `pack_faq`: el negocio
debe haber nutrido su conocimiento.

La herramienta RECUPERA las entradas relevantes y se las da al modelo para que COMPONGA la respuesta;
no redacta. Si no hay información suficiente, devuelve una señal clara de NO inventar (ofrecer un humano
o decir que no se tiene ese dato). La recuperación vive detrás del puerto `Recuperador` del servicio
(keyword v1; embeddings/RAG v2) — la herramienta no cambia al cambiar el mecanismo.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.llm.base import ToolCall, ToolSpec
from modules.faq.service import FaqService

# Tope de entradas y de caracteres por entrada que se devuelven al modelo (no ahogar el prompt).
_MAX_ENTRADAS = 5
_MAX_CHARS = 600
# Señal de "sin información": el modelo NO debe inventar.
_SIN_INFO = (
    "No tengo información registrada sobre eso. No inventes: ofrece pasar la conversación a un asesor "
    "(escalar_humano) o dile con amabilidad que no tienes ese dato."
)


@dataclass(frozen=True, slots=True)
class FaqDeps:
    """Dependencias del turno: el servicio de FAQ atado a la sesión del tenant."""

    faq: FaqService


class ResponderFaqArgs(BaseModel):
    pregunta: str = Field(min_length=1)  # la duda del cliente, tal como la entendió el agente


async def _responder_faq(
    args: ResponderFaqArgs, ctx: Contexto, deps: FaqDeps
) -> Resultado | ErrorTool:
    resultado = await deps.faq.responder(args.pregunta)
    if not resultado.hay_info:
        return Resultado(data={"entradas": []}, resumen=_SIN_INFO)
    entradas = [
        {"titulo": e.titulo, "contenido": e.contenido[:_MAX_CHARS]}
        for e in resultado.entradas[:_MAX_ENTRADAS]
    ]
    return Resultado(
        data={"entradas": entradas},
        resumen=(
            "Información del negocio encontrada; responde SOLO con base en estas entradas: "
            + " | ".join(e["titulo"] for e in entradas) + "."
        ),
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, FaqDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class FaqTool:
    """Herramienta del runtime: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_faq"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_FAQ: tuple[FaqTool, ...] = (
    FaqTool(
        nombre="responder_faq",
        descripcion=(
            "Consulta el conocimiento del negocio para responder dudas generales (ubicación, horarios, "
            "precios, formas de pago, parqueo, políticas…). Devuelve entradas; responde SOLO con base en "
            "ellas. Si no hay información, NO inventes: ofrece un asesor humano o di que no tienes el dato."
        ),
        args_model=ResponderFaqArgs,
        handler=_responder_faq,
    ),
)

POR_NOMBRE: dict[str, FaqTool] = {t.nombre: t for t in CATALOGO_FAQ}


def catalogo_visible(ctx: Contexto) -> list[FaqTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_faq`)."""
    return [t for t in CATALOGO_FAQ if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: FaqDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler."""
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
