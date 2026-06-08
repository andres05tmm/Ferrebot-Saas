"""Herramientas de agente del pack Agenda/Citas (Capa 3 del doc), de cara al cliente en WhatsApp.

Capa fina sobre el motor (`modules/agenda/service.py`): cada herramienta traduce los args del modelo
a una llamada al servicio y normaliza la salida al envelope común (`ai/envelope.py`). NO reimplementa
lógica de agenda (cálculo de cupos, locks, políticas) — eso vive en el motor.

GUARDARRAÍL DE SEGURIDAD (no negociable): el **tenant** y el **teléfono del cliente** viajan en el
`Contexto` que inyecta el adaptador de canal (el número que escribe), **nunca** como args del modelo.
`mis_citas`/`reagendar_cita`/`cancelar_cita` operan SOLO sobre las citas de ese teléfono (el motor ya
filtra por `telefono`); el modelo no puede pasar otro número ni ver/tocar citas ajenas. Estas
herramientas son de cara al público: se acotan por teléfono, no por RBAC de staff. Se exponen solo si
la empresa tiene el flag `pack_agenda`.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import COLOMBIA_TZ, to_co
from core.llm.base import ToolCall, ToolSpec
from modules.agenda.errors import (
    CitaInexistente,
    CitaNoModificable,
    CupoNoDisponible,
    FueraDePoliticaCancelacion,
    RecursoInexistente,
    RecursoNoPrestaServicio,
    ReagendarNoPermitido,
    ServicioInexistente,
)
from modules.agenda.service import AgendaService

# Tope de cupos/alternativas que se devuelven al modelo (no ahogar el prompt).
_MAX_SLOTS = 8
_MAX_ALTERNATIVAS = 3
_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


@dataclass(frozen=True, slots=True)
class AgendaDeps:
    """Dependencias del turno para el pack: el motor atado a la sesión del tenant."""

    agenda: AgendaService


# --- helpers ----------------------------------------------------------------
def _a_co(dt: datetime) -> datetime:
    """Normaliza a hora Colombia (lo naive se asume ya en Colombia); espeja al motor."""
    return dt.replace(tzinfo=COLOMBIA_TZ) if dt.tzinfo is None else to_co(dt)


def _fmt(dt: datetime) -> str:
    """Fecha/hora legible para el cliente: 'vie 12/06 14:00' (hora Colombia)."""
    d = to_co(dt)
    return f"{_DIAS[d.weekday()]} {d.day:02d}/{d.month:02d} {d.hour:02d}:{d.minute:02d}"


def _iso(dt: datetime) -> str:
    """ISO 8601 SIEMPRE en hora Colombia (lo guardado vuelve en UTC): formato único y round-trip."""
    return to_co(dt).isoformat()


def _telefono(ctx: Contexto) -> str | None:
    """Teléfono del cliente del contexto del canal. None → el runtime no lo inyectó (falla cerrado)."""
    return ctx.cliente_telefono or None


def _error_cupo(inicio: datetime, alternativas: list[datetime]) -> ErrorTool:
    """Mapea CupoNoDisponible a un error recuperable con alternativas que el agente puede ofrecer."""
    if alternativas:
        alt = ", ".join(_fmt(a) for a in alternativas[:_MAX_ALTERNATIVAS])
        detalle = f"El horario {_fmt(inicio)} no está disponible. Alternativas: {alt}."
    else:
        detalle = f"El horario {_fmt(inicio)} no está disponible y no veo alternativas cercanas."
    return ErrorTool("cupo_no_disponible", detalle, recuperable=True)


_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)


# --- args (lo único que provee el modelo; el teléfono NUNCA va aquí) ---------
class ListarServiciosArgs(BaseModel):
    """Sin parámetros."""


class ConsultarDisponibilidadArgs(BaseModel):
    servicio_id: int
    desde: date | None = None           # default: hoy (lo resuelve el motor)
    hasta: date | None = None           # default: igual a `desde`
    recurso_id: int | None = None       # opcional: limita a un recurso


class AgendarCitaArgs(BaseModel):
    servicio_id: int
    inicio: datetime                    # uno de los cupos de consultar_disponibilidad
    nombre: str = Field(min_length=1)   # a nombre de quién (lo dice el cliente)
    recurso_id: int | None = None       # opcional: si se omite, se elige uno que ofrezca el cupo


class MisCitasArgs(BaseModel):
    """Sin parámetros: SIEMPRE las del teléfono del contexto."""


class ReagendarCitaArgs(BaseModel):
    cita_id: int                        # referencia de la cita (de mis_citas)
    nuevo_inicio: datetime


class CancelarCitaArgs(BaseModel):
    cita_id: int                        # referencia de la cita (de mis_citas)


class ReconfirmarCitaArgs(BaseModel):
    cita_id: int                        # referencia de la cita (de mis_citas)


# --- handlers ---------------------------------------------------------------
async def _listar_servicios(
    args: ListarServiciosArgs, ctx: Contexto, deps: AgendaDeps
) -> Resultado | ErrorTool:
    servicios = await deps.agenda.listar_servicios()
    if not servicios:
        return Resultado(data={"servicios": []}, resumen="Por ahora no hay servicios para agendar.")
    data = [
        {
            "id": s.id, "nombre": s.nombre, "duracion_min": s.duracion_min,
            "precio": str(s.precio) if s.precio is not None else None,
        }
        for s in servicios
    ]
    partes = [
        f"{s.nombre} ({s.duracion_min} min" + (f", ${s.precio})" if s.precio is not None else ")")
        for s in servicios
    ]
    return Resultado(data={"servicios": data}, resumen="Servicios: " + "; ".join(partes) + ".")


async def _consultar_disponibilidad(
    args: ConsultarDisponibilidadArgs, ctx: Contexto, deps: AgendaDeps
) -> Resultado | ErrorTool:
    try:
        slots = await deps.agenda.calcular_disponibilidad(
            args.servicio_id, desde=args.desde, hasta=args.hasta, recurso_id=args.recurso_id
        )
    except ServicioInexistente as exc:
        return ErrorTool("servicio_no_encontrado", str(exc), recuperable=True)
    except RecursoInexistente as exc:
        return ErrorTool("recurso_no_encontrado", str(exc), recuperable=True)
    except RecursoNoPrestaServicio as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    if not slots:
        return Resultado(data={"slots": []}, resumen="No hay cupos libres en ese rango. ¿Otra fecha?")
    recortados = slots[:_MAX_SLOTS]
    data = [{"inicio": _iso(s.inicio), "recurso_id": s.recurso_id} for s in recortados]
    return Resultado(
        data={"slots": data},
        resumen="Cupos disponibles: " + ", ".join(_fmt(s.inicio) for s in recortados) + ".",
    )


async def _agendar_cita(
    args: AgendarCitaArgs, ctx: Contexto, deps: AgendaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    inicio = _a_co(args.inicio)

    recurso_id = args.recurso_id
    if recurso_id is None:
        # Sin recurso explícito: se elige uno que ofrezca exactamente ese cupo (el doc lo permite).
        try:
            slots = await deps.agenda.calcular_disponibilidad(
                args.servicio_id, desde=inicio.date(), hasta=inicio.date()
            )
        except ServicioInexistente as exc:
            return ErrorTool("servicio_no_encontrado", str(exc), recuperable=True)
        coincidencias = [s for s in slots if s.inicio == inicio]
        if not coincidencias:
            return _error_cupo(inicio, [s.inicio for s in slots])
        recurso_id = coincidencias[0].recurso_id

    try:
        res = await deps.agenda.validar_y_agendar(
            servicio_id=args.servicio_id, recurso_id=recurso_id, inicio=inicio,
            cliente_nombre=args.nombre, cliente_telefono=telefono,
            idempotency_key=ctx.idempotency_key, origen="whatsapp",
        )
    except CupoNoDisponible as exc:
        return _error_cupo(exc.inicio, exc.alternativas)
    except ServicioInexistente as exc:
        return ErrorTool("servicio_no_encontrado", str(exc), recuperable=True)
    except RecursoInexistente as exc:
        return ErrorTool("recurso_no_encontrado", str(exc), recuperable=True)
    except RecursoNoPrestaServicio as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    c = res.cita
    return Resultado(
        data={
            "cita_id": c.id, "servicio_id": c.servicio_id, "recurso_id": c.recurso_id,
            "inicio": _iso(c.inicio), "fin": _iso(c.fin), "estado": c.estado,
        },
        resumen=f"Listo {c.cliente_nombre} ✅ Cita #{c.id} para el {_fmt(c.inicio)} ({c.estado}).",
        evento="cita_agendada",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _mis_citas(args: MisCitasArgs, ctx: Contexto, deps: AgendaDeps) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    citas = await deps.agenda.proximas_citas(telefono)
    if not citas:
        return Resultado(data={"citas": []}, resumen="No tienes citas próximas.")
    data = [
        {"cita_id": c.id, "servicio_id": c.servicio_id, "inicio": _iso(c.inicio), "estado": c.estado}
        for c in citas
    ]
    resumen = "Tus próximas citas: " + "; ".join(
        f"#{c.id} {_fmt(c.inicio)} ({c.estado})" for c in citas
    ) + "."
    return Resultado(data={"citas": data}, resumen=resumen)


async def _reagendar_cita(
    args: ReagendarCitaArgs, ctx: Contexto, deps: AgendaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        cita = await deps.agenda.reagendar(args.cita_id, args.nuevo_inicio, telefono=telefono)
    except CitaInexistente as exc:
        return ErrorTool("cita_no_encontrada", str(exc), recuperable=True)
    except CitaNoModificable as exc:
        return ErrorTool("cita_no_modificable", str(exc), recuperable=False)
    except ReagendarNoPermitido as exc:
        return ErrorTool("reagendar_no_permitido", str(exc), recuperable=False)
    except FueraDePoliticaCancelacion as exc:
        return ErrorTool("fuera_de_politica", str(exc), recuperable=True)
    except CupoNoDisponible as exc:
        return _error_cupo(exc.inicio, exc.alternativas)
    return Resultado(
        data={"cita_id": cita.id, "inicio": _iso(cita.inicio), "estado": cita.estado},
        resumen=f"Tu cita #{cita.id} quedó para el {_fmt(cita.inicio)}.",
        evento="cita_reagendada",
        idempotente="aplicada",
    )


async def _cancelar_cita(
    args: CancelarCitaArgs, ctx: Contexto, deps: AgendaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        cita = await deps.agenda.cancelar(args.cita_id, telefono=telefono)
    except CitaInexistente as exc:
        return ErrorTool("cita_no_encontrada", str(exc), recuperable=True)
    except CitaNoModificable as exc:
        return ErrorTool("cita_no_modificable", str(exc), recuperable=False)
    except FueraDePoliticaCancelacion as exc:
        return ErrorTool("fuera_de_politica", str(exc), recuperable=True)
    return Resultado(
        data={"cita_id": cita.id, "estado": cita.estado},
        resumen=f"Cancelé tu cita #{cita.id}.",
        evento="cita_estado",
        idempotente="aplicada",
    )


async def _reconfirmar_cita(
    args: ReconfirmarCitaArgs, ctx: Contexto, deps: AgendaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        cita = await deps.agenda.reconfirmar(args.cita_id, telefono=telefono)
    except CitaInexistente as exc:
        return ErrorTool("cita_no_encontrada", str(exc), recuperable=True)
    except CitaNoModificable as exc:
        return ErrorTool("cita_no_modificable", str(exc), recuperable=False)
    return Resultado(
        data={"cita_id": cita.id, "confirmacion": cita.confirmacion},
        resumen=f"¡Listo! Tu cita #{cita.id} quedó reconfirmada ✅. Te esperamos.",
        evento="cita_confirmacion",
        idempotente="aplicada",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, AgendaDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class AgendaTool:
    """Herramienta del pack: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_agenda"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_AGENDA: tuple[AgendaTool, ...] = (
    AgendaTool(
        nombre="listar_servicios",
        descripcion="Lista los servicios que se pueden agendar (nombre, duración, precio). Solo lectura.",
        args_model=ListarServiciosArgs, handler=_listar_servicios,
    ),
    AgendaTool(
        nombre="consultar_disponibilidad",
        descripcion=(
            "Consulta cupos libres de un servicio en una fecha o rango (opcional: un recurso). "
            "Devuelve horarios; nunca los calcules tú. Solo lectura."
        ),
        args_model=ConsultarDisponibilidadArgs, handler=_consultar_disponibilidad,
    ),
    AgendaTool(
        nombre="agendar_cita",
        descripcion=(
            "Agenda una cita en un cupo de consultar_disponibilidad, a nombre del cliente. "
            "Si el cupo está ocupado, devuelve alternativas."
        ),
        args_model=AgendarCitaArgs, handler=_agendar_cita,
    ),
    AgendaTool(
        nombre="mis_citas",
        descripcion="Lista las próximas citas del cliente que escribe. Solo lectura.",
        args_model=MisCitasArgs, handler=_mis_citas,
    ),
    AgendaTool(
        nombre="reagendar_cita",
        descripcion="Mueve una cita del cliente a un nuevo horario (aplica la política de cambios).",
        args_model=ReagendarCitaArgs, handler=_reagendar_cita,
    ),
    AgendaTool(
        nombre="cancelar_cita",
        descripcion="Cancela una cita del cliente (aplica la política de cancelación).",
        args_model=CancelarCitaArgs, handler=_cancelar_cita,
    ),
    AgendaTool(
        nombre="reconfirmar_cita",
        descripcion=(
            "Confirma la asistencia del cliente a una cita próxima cuando responde 'sí' a un "
            "recordatorio (sí/confirmo/ahí estaré). Usa mis_citas para hallar su próxima cita."
        ),
        args_model=ReconfirmarCitaArgs, handler=_reconfirmar_cita,
    ),
)

POR_NOMBRE: dict[str, AgendaTool] = {t.nombre: t for t in CATALOGO_AGENDA}


def catalogo_visible(ctx: Contexto) -> list[AgendaTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_agenda`)."""
    return [t for t in CATALOGO_AGENDA if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: AgendaDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler.

    Punto de entrada para el runtime del agente y los tests. Los args inválidos del modelo vuelven
    como error recuperable; cualquier `cliente_telefono` que el modelo intente colar se ignora (no
    está en ningún args_model) — la identidad sale SIEMPRE del `Contexto`.
    """
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
