"""Herramienta TRANSVERSAL del runtime de cara al cliente: `escalar_humano` (handoff).

No pertenece a un pack de dominio: cualquier agente (agenda, catálogo, FAQ…) debe poder pasar la
conversación a un humano cuando no resuelve, cuando el cliente pide un asesor, o ante quejas/temas
fuera de scope (espeja las reglas [TRANSFER] de Palmarito, `src/anthropic.js`). Por eso es de NÚCLEO
del runtime (`feature=None`): siempre disponible, aunque la empresa no tenga packs.

GUARDARRAÍL DE SEGURIDAD (no negociable): el **tenant** y el **teléfono del cliente** viajan en el
`Contexto` que inyecta el adaptador de canal (el número que escribe), NUNCA como args del modelo. El
modelo solo aporta el `motivo`; no puede escalar la conversación de otro número.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.llm.base import ToolCall, ToolSpec
from modules.conversaciones.service import ConversacionService

# Mensaje para el cliente al escalar (lo incorpora el modelo a su respuesta).
_MENSAJE_CLIENTE = "Voy a conectarte con un asesor humano; te escribirán muy pronto. 🙌"

_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)


@dataclass(frozen=True, slots=True)
class HandoffDeps:
    """Dependencias del turno: el motor de conversación atado a la sesión del tenant."""

    conversaciones: ConversacionService


# --- args (lo único que provee el modelo; el teléfono NUNCA va aquí) ---------
class EscalarHumanoArgs(BaseModel):
    motivo: str = Field(min_length=1)  # por qué escala (no resuelve, piden asesor, queja, fuera de scope)


# --- handler ----------------------------------------------------------------
async def _escalar_humano(
    args: EscalarHumanoArgs, ctx: Contexto, deps: HandoffDeps
) -> Resultado | ErrorTool:
    telefono = ctx.cliente_telefono or None
    if telefono is None:
        return _SIN_TELEFONO
    await deps.conversaciones.escalar(telefono, motivo=args.motivo)
    return Resultado(
        data={"estado": "humano"},
        resumen=_MENSAJE_CLIENTE,
        evento="conversacion_escalada",
        idempotente="aplicada",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, HandoffDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class HandoffTool:
    """Herramienta del runtime: lo que ve el modelo (spec) + su handler. `feature=None` → núcleo."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str | None = None  # núcleo: siempre disponible (todo agente puede escalar a humano)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_HANDOFF: tuple[HandoffTool, ...] = (
    HandoffTool(
        nombre="escalar_humano",
        descripcion=(
            "Pasa la conversación a un asesor humano. Úsala cuando NO puedas resolver el pedido, "
            "cuando el cliente pida explícitamente un humano/asesor, o ante quejas o temas fuera de "
            "tu alcance. Indica un 'motivo' breve. Tras llamarla, el negocio continuará la atención."
        ),
        args_model=EscalarHumanoArgs,
        handler=_escalar_humano,
    ),
)

POR_NOMBRE: dict[str, HandoffTool] = {t.nombre: t for t in CATALOGO_HANDOFF}


def catalogo_visible(ctx: Contexto) -> list[HandoffTool]:
    """Herramientas del runtime visibles para la empresa (las de núcleo siempre lo están)."""
    return [t for t in CATALOGO_HANDOFF if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo, listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: HandoffDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler.

    Cualquier `cliente_telefono` que el modelo intente colar se ignora (no está en el args_model) —
    la identidad sale SIEMPRE del `Contexto`.
    """
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
