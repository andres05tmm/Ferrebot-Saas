"""Herramienta del pack Postventa (plan §2.6), de cara al cliente en WhatsApp.

Una sola herramienta: `calificar_atencion` — registra la calificación 1-5 (+ comentario) cuando el
cliente responde a la encuesta de seguimiento. Si la calificación alcanza el umbral del negocio y
hay link de Google Maps, la herramienta lo devuelve para que el agente pida la reseña.

GUARDARRAÍL: el teléfono viaja en el `Contexto` del canal (la respuesta es SIEMPRE del que
escribe). Habeas Data: solo teléfono + calificación + comentario. Flag `pack_postventa`.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.llm.base import ToolCall, ToolSpec
from modules.postventa.service import PostventaService


@dataclass(frozen=True, slots=True)
class PostventaDeps:
    """Dependencias del turno: el motor de postventa atado a la sesión del tenant."""

    postventa: PostventaService


_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)


class CalificarArgs(BaseModel):
    calificacion: int = Field(ge=1, le=5)            # 1 (mal) … 5 (excelente)
    comentario: str = Field(default="", max_length=500)


async def _calificar(
    args: CalificarArgs, ctx: Contexto, deps: PostventaDeps
) -> Resultado | ErrorTool:
    telefono = ctx.cliente_telefono or None
    if telefono is None:
        return _SIN_TELEFONO
    respuesta, link = await deps.postventa.calificar(
        telefono, args.calificacion, comentario=args.comentario or None
    )
    if link:
        resumen = (
            f"Calificación {args.calificacion}/5 registrada ✅. Agradécele con calidez e invítalo "
            f"a dejar una reseña pública aquí: {link}"
        )
    elif args.calificacion <= 2:
        resumen = (
            f"Calificación {args.calificacion}/5 registrada. Discúlpate con sinceridad, agradece "
            "el comentario y ofrece pasar el caso a un humano (escalar_humano) para resolverlo."
        )
    else:
        resumen = f"Calificación {args.calificacion}/5 registrada ✅. Agradécele su tiempo."
    return Resultado(
        data={"respuesta_id": respuesta.id, "calificacion": args.calificacion,
              "link_resena": link},
        resumen=resumen,
        evento="encuesta_respondida",
        idempotente="aplicada",
    )


Handler = Callable[[BaseModel, Contexto, PostventaDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class PostventaTool:
    """Herramienta del pack: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_postventa"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_POSTVENTA: tuple[PostventaTool, ...] = (
    PostventaTool(
        nombre="calificar_atencion",
        descripcion=(
            "Registra la calificación (1 a 5) que el cliente da a su última atención/compra, con "
            "comentario opcional. Úsala cuando responda a la encuesta de seguimiento (un número, "
            "'muy bien', 'mala', etc. → tradúcelo a 1-5 y confírmalo si es ambiguo)."
        ),
        args_model=CalificarArgs, handler=_calificar,
    ),
)

POR_NOMBRE: dict[str, PostventaTool] = {t.nombre: t for t in CATALOGO_POSTVENTA}


def catalogo_visible(ctx: Contexto) -> list[PostventaTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_postventa`)."""
    return [t for t in CATALOGO_POSTVENTA if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: PostventaDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler."""
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
