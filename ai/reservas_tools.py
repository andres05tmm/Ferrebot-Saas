"""Herramientas de agente del pack Reservas (plan §2.7), de cara al huésped en WhatsApp.

Variante NOCHES del motor de agenda: `consultar_noches` (habitaciones libres con tarifa) y
`reservar_habitacion` (lock + cita check-in→check-out). Ver/cancelar la reserva son las
herramientas de agenda de siempre (mis_citas/cancelar_cita): una reserva ES una cita.

GUARDARRAÍL (no negociable): el **tenant** y el **teléfono** viajan en el `Contexto`; el agente
nunca calcula tarifas ni disponibilidad (solo el motor). Con `requiere_anticipo` y el frente de
pagos activo, la reserva nace `pendiente` y la herramienta crea el cobro del anticipo (link real
si el tenant tiene PSP). Se exponen solo con el flag `pack_reservas`.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.llm.base import ToolCall, ToolSpec
from modules.agenda.errors import CupoNoDisponible, RecursoInexistente
from modules.pagos.service import PagosService
from modules.reservas.service import NochesInvalidas, ReservasService


@dataclass(frozen=True, slots=True)
class ReservasDeps:
    """Dependencias del turno: el motor de reservas (+ pagos OPCIONAL para el anticipo)."""

    reservas: ReservasService
    pagos: PagosService | None = None


def _pesos(monto) -> str:
    return "$" + f"{Decimal(monto):,.0f}".replace(",", ".")


def _telefono(ctx: Contexto) -> str | None:
    return ctx.cliente_telefono or None


_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)


# --- args (lo único que provee el modelo; el teléfono NUNCA va aquí) ---------
class ConsultarNochesArgs(BaseModel):
    checkin: date                                  # fecha de llegada
    noches: int = Field(ge=1, le=30)


class ReservarHabitacionArgs(BaseModel):
    recurso_id: int                                # de consultar_noches
    checkin: date
    noches: int = Field(ge=1, le=30)
    nombre: str = Field(min_length=1, max_length=80)


# --- handlers ---------------------------------------------------------------
async def _consultar_noches(
    args: ConsultarNochesArgs, ctx: Contexto, deps: ReservasDeps
) -> Resultado | ErrorTool:
    try:
        libres = await deps.reservas.habitaciones_libres(args.checkin, args.noches)
    except NochesInvalidas:
        return ErrorTool("validacion", "La estadía debe ser de 1 a 30 noches.", recuperable=True)
    if not libres:
        return Resultado(
            data={"habitaciones": []},
            resumen=f"No hay habitaciones libres para el {args.checkin} ({args.noches} noches). "
                    "Ofrece otras fechas.",
        )
    data = [
        {"recurso_id": h.recurso_id, "nombre": h.nombre,
         "precio_noche": str(h.precio_noche) if h.precio_noche is not None else None,
         "total": str(h.total) if h.total is not None else None}
        for h in libres
    ]
    partes = [
        h.nombre + (f" ({_pesos(h.precio_noche)}/noche, total {_pesos(h.total)})"
                    if h.precio_noche is not None else "")
        for h in libres
    ]
    return Resultado(
        data={"habitaciones": data},
        resumen=f"Disponible para el {args.checkin} ({args.noches} noches): " + "; ".join(partes) + ".",
    )


async def _reservar_habitacion(
    args: ReservarHabitacionArgs, ctx: Contexto, deps: ReservasDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        res = await deps.reservas.reservar(
            recurso_id=args.recurso_id, checkin=args.checkin, noches=args.noches,
            cliente_nombre=args.nombre, cliente_telefono=telefono,
            idempotency_key=ctx.idempotency_key,
        )
    except NochesInvalidas:
        return ErrorTool("validacion", "La estadía debe ser de 1 a 30 noches.", recuperable=True)
    except RecursoInexistente as exc:
        return ErrorTool("recurso_no_encontrado", str(exc), recuperable=True)
    except CupoNoDisponible:
        return ErrorTool(
            "cupo_no_disponible",
            "Esa habitación ya no está libre para esas fechas. Vuelve a consultar disponibilidad.",
            recuperable=True,
        )
    cita = res.cita
    resumen = (
        f"Reserva #{cita.id} creada para {args.nombre} ✅ check-in {args.checkin}, "
        f"{args.noches} noche(s) ({cita.estado})."
    )
    data = {
        "reserva_id": cita.id, "estado": cita.estado,
        "checkin": str(args.checkin), "noches": args.noches,
        "anticipo": str(res.anticipo) if res.anticipo is not None else None,
    }
    # Anticipo (ADR 0013): con pagos activos se crea el cobro; con PSP el link viaja por el chat.
    if res.anticipo is not None and deps.pagos is not None and ctx.tiene_capacidad("pagos_online"):
        cobro = await deps.pagos.crear_cobro(
            origen="cita", origen_id=cita.id, monto=res.anticipo,
            descripcion=f"Anticipo reserva #{cita.id}", cliente_telefono=telefono,
        )
        data["cobro"] = {"cobro_id": cobro.id, "url": cobro.url, "estado": cobro.estado}
        resumen += f" Para confirmarla se requiere un anticipo de {_pesos(res.anticipo)}."
        if cobro.url:
            resumen += f" Puede pagarlo aquí: {cobro.url}"
    elif res.anticipo is not None:
        resumen += (
            f" Para confirmarla se requiere un anticipo de {_pesos(res.anticipo)}; "
            "el negocio le indicará cómo pagarlo."
        )
    return Resultado(
        data=data, resumen=resumen, evento="cita_agendada",
        idempotente="duplicada" if res.replay else "aplicada",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, ReservasDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class ReservasTool:
    """Herramienta del pack: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_reservas"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_RESERVAS: tuple[ReservasTool, ...] = (
    ReservasTool(
        nombre="consultar_noches",
        descripcion=(
            "Consulta qué habitaciones están libres para una fecha de llegada y N noches, con su "
            "tarifa por noche y el total. Devuelve disponibilidad real; nunca la calcules tú. Solo lectura."
        ),
        args_model=ConsultarNochesArgs, handler=_consultar_noches,
    ),
    ReservasTool(
        nombre="reservar_habitacion",
        descripcion=(
            "Reserva una habitación de consultar_noches para el huésped (check-in + noches + nombre). "
            "Si el negocio exige anticipo, la reserva queda pendiente hasta el pago."
        ),
        args_model=ReservarHabitacionArgs, handler=_reservar_habitacion,
    ),
)

POR_NOMBRE: dict[str, ReservasTool] = {t.nombre: t for t in CATALOGO_RESERVAS}


def catalogo_visible(ctx: Contexto) -> list[ReservasTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_reservas`)."""
    return [t for t in CATALOGO_RESERVAS if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: ReservasDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler."""
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
