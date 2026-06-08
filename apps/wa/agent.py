"""Runtime del agente de WhatsApp: conecta el cerebro LLM (core/llm) con las herramientas del pack
Agenda (`ai/agenda_tools`) sobre el canal Kapso. NO reimplementa lógica de agenda: orquesta.

Piezas:
  - `correr_bucle`: bucle LLM genérico (generate → tool_calls → tool_result → repite → texto final),
    acotado por `max_iters`. Realimenta el envelope de cada herramienta al modelo.
  - `construir_system`: prompt de asistente ESPECIALIZADO en agenda/atención (cumple la regla de Meta:
    nada de propósito general), con la persona/tono del negocio (`agenda_config.persona`).
  - `MemoriaWa`: historial de conversación por (tenant, cliente_telefono) en Redis, con TTL (la
    reserva se da en varios mensajes). Guarda solo los turnos user/assistant (no el andamiaje de tools).
  - `AgenteWa`: arma el Contexto (tenant + cliente_telefono + capacidades), monta el catálogo por flag,
    corre el bucle sobre la sesión del tenant (la cita se persiste al cerrarla) y responde por Kapso.
    Fallback elegante: ante cualquier fallo, un mensaje amable (sin exponer errores internos).
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ai.agenda_tools import AgendaDeps, ejecutar as agenda_ejecutar, exponer_catalogo as exponer_agenda
from ai.envelope import Contexto, ErrorTool, Resultado
from ai.faq_tools import (
    FaqDeps,
    POR_NOMBRE as FAQ_POR_NOMBRE,
    ejecutar as faq_ejecutar,
    exponer_catalogo as exponer_faq,
)
from ai.handoff_tools import (
    HandoffDeps,
    POR_NOMBRE as HANDOFF_POR_NOMBRE,
    ejecutar as handoff_ejecutar,
    exponer_catalogo as exponer_handoff,
)
from apps.wa.kapso import KapsoSender, MensajeWa
from core.config.timezone import now_co
from core.llm.base import Message, ToolSpec
from core.llm.factory import LLMResuelto, Turno
from core.logging import get_logger
from core.tenancy.context import ResolvedTenant
from modules.agenda.gcal import CalendarPort
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.service import AgendaService
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.service import ConversacionService
from modules.faq.repository import SqlConocimientoRepository
from modules.faq.service import FaqService

log = get_logger("wa.agent")

# Mensaje amable si el modelo o el canal fallan (nunca se expone el error interno).
FALLBACK = "Disculpa, tuve un problema para atenderte. ¿Puedes intentarlo de nuevo en un momento?"

# Tope de iteraciones modelo↔herramientas por mensaje (agendar puede encadenar varias consultas).
_MAX_ITERS = 6

_SYSTEM_BASE = (
    "Eres un asistente virtual de citas por WhatsApp para un negocio de servicios. Tu ÚNICO propósito "
    "es ayudar al cliente a: ver los servicios, consultar horarios disponibles, y agendar, reagendar "
    "o cancelar SUS citas. No respondes temas ajenos a la agenda.\n"
    "Reglas: usa SIEMPRE las herramientas para consultar disponibilidad y para agendar/cambiar citas; "
    "nunca inventes horarios, precios ni confirmes una cita sin la herramienta. Pide los datos que "
    "falten (servicio, fecha/hora, nombre) antes de agendar. Responde en español, breve y cordial; "
    "las fechas y horas son de Colombia. Si te piden algo fuera de las citas, di con amabilidad que "
    "solo puedes ayudar con la agenda.\n"
    "Para dudas generales del negocio (ubicación, horarios, precios, formas de pago, parqueo, "
    "políticas) usa responder_faq y responde SOLO con esa información. Si no hay información suficiente, "
    "NO inventes: ofrece pasar a un asesor humano (escalar_humano) o di que no tienes ese dato.\n"
    "Si el cliente responde a un RECORDATORIO de su cita: si confirma que asistirá (sí, confirmo, ahí "
    "estaré, dale) usa mis_citas para hallar su próxima cita y reconfírmala con reconfirmar_cita; si "
    "dice que no podrá o quiere cancelar, cancélala con cancelar_cita. Si quiere otro horario, reagenda."
)


_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _ancla_fecha() -> str:
    """Ancla de fecha/hora ACTUAL de Colombia (se calcula por mensaje, no al importar el módulo).

    Sin esto el modelo no resuelve fechas relativas ('hoy', 'mañana', 'el 9 de junio', 'lo más pronto
    posible') y consultar_disponibilidad sale vacía.
    """
    ahora = now_co()
    return (
        f"Hoy es {_DIAS[ahora.weekday()]}, {ahora.day} de {_MESES[ahora.month - 1]} de {ahora.year}, "
        f"{ahora:%H:%M} hora de Colombia. Resuelve fechas relativas (hoy, mañana, 'el 9 de junio', "
        "'lo más pronto posible') con base en esta fecha."
    )


def construir_system(persona: str | None) -> str:
    """System prompt del asistente especializado + ancla de fecha de hoy + persona del negocio."""
    base = f"{_SYSTEM_BASE}\n\n{_ancla_fecha()}"
    if persona:
        return f"{base}\n\nTono e identidad del negocio: {persona}"
    return base


def whatsappify(texto: str) -> str:
    """Adapta el Markdown del modelo a lo que WhatsApp SÍ renderiza (reimpl. de Palmarito src/bot.js).

    - `[texto](url)` → "texto: url"   (WhatsApp no hace links Markdown)
    - `***x***` / `**x**` → `*x*`      (WhatsApp usa UN asterisco para negrita)
    - encabezados (`#`…`######`) → se quita el marcador y se conserva el texto
    - separadores (`---` en su propia línea) → se eliminan
    """
    if not texto:
        return texto
    texto = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1: \2", texto)            # links → "texto: url"
    texto = re.sub(r"\*\*\*(.+?)\*\*\*", r"*\1*", texto, flags=re.DOTALL)   # ***bold*** → *bold*
    texto = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto, flags=re.DOTALL)       # **bold** → *bold*
    lineas: list[str] = []
    for linea in texto.split("\n"):
        if re.fullmatch(r"\s*-{3,}\s*", linea):          # separador horizontal → fuera
            continue
        lineas.append(re.sub(r"^\s*#{1,6}\s*", "", linea))  # encabezado → solo su texto
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()


def _envelope_json(resultado: Resultado | ErrorTool) -> str:
    """Serializa el resultado de una herramienta al envelope que ve el modelo en el tool_result."""
    if isinstance(resultado, Resultado):
        return json.dumps(
            {"ok": True, "data": resultado.data, "resumen": resultado.resumen},
            ensure_ascii=False, default=str,
        )
    return json.dumps(
        {"ok": False, "error": resultado.error, "detail": resultado.detail,
         "recuperable": resultado.recuperable},
        ensure_ascii=False,
    )


@dataclass(frozen=True, slots=True)
class RuntimeDeps:
    """Dependencias de TODOS los packs del runtime de cara al cliente (agenda + handoff + FAQ transversales)."""

    agenda: AgendaDeps
    handoff: HandoffDeps
    faq: FaqDeps


def exponer_runtime(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo: pack(s) de dominio + transversales (FAQ gateada por flag, handoff núcleo)."""
    return [*exponer_agenda(ctx), *exponer_faq(ctx), *exponer_handoff(ctx)]


async def ejecutar_runtime(
    tool_call: Any, ctx: Contexto, deps: RuntimeDeps
) -> Resultado | ErrorTool:
    """Despacha la herramienta al pack que la define (handoff/FAQ transversales o agenda)."""
    if tool_call.name in HANDOFF_POR_NOMBRE:
        return await handoff_ejecutar(tool_call, ctx, deps.handoff)
    if tool_call.name in FAQ_POR_NOMBRE:
        return await faq_ejecutar(tool_call, ctx, deps.faq)
    return await agenda_ejecutar(tool_call, ctx, deps.agenda)


# Firma del ejecutor de herramientas (lo satisface `ejecutar_runtime` y los fakes de test).
Ejecutar = Callable[[Any, Contexto, Any], Awaitable[Resultado | ErrorTool]]


async def correr_bucle(
    *,
    proveedor: LLMResuelto,
    system: str,
    tools: list[ToolSpec],
    ctx: Contexto,
    deps: Any,
    historial: list[Message],
    texto: str,
    ejecutar: Ejecutar = ejecutar_runtime,
    max_iters: int = _MAX_ITERS,
) -> str:
    """Bucle agente: el modelo pide herramientas, se ejecutan y se realimentan, hasta el texto final.

    Devuelve el texto para el cliente. El andamiaje (assistant con tool_calls + tool_result) es
    EFÍMERO del turno; solo el texto final se persiste en la memoria de conversación.
    """
    mensajes: list[Message] = [*historial, Message(role="user", content=texto)]
    for _ in range(max_iters):
        resp = await proveedor.provider.generate(
            messages=mensajes, tools=tools, model=proveedor.model, system=system
        )
        if not resp.tool_calls:
            return resp.text or FALLBACK
        # Tripleta tool_use→tool_result: assistant con sus tool_calls + un tool_result por cada uno.
        mensajes.append(
            Message(role="assistant", content=resp.text or "", tool_calls=list(resp.tool_calls))
        )
        for call in resp.tool_calls:
            resultado = await ejecutar(call, ctx, deps)
            mensajes.append(
                Message(role="tool", content=_envelope_json(resultado),
                        tool_call_id=call.id, name=call.name)
            )
    # Tope alcanzado: una generación final SIN herramientas para cerrar con texto.
    resp = await proveedor.provider.generate(
        messages=mensajes, tools=[], model=proveedor.model, system=system
    )
    return resp.text or FALLBACK


class MemoriaWa:
    """Historial de conversación por (tenant, cliente_telefono) en Redis, con TTL.

    Persiste solo turnos `user`/`assistant` (texto): basta para que el modelo recuerde el hilo entre
    mensajes (p. ej. "¿a nombre de quién?") sin arrastrar el andamiaje de herramientas. Cliente Redis
    perezoso e inyectable (tests). Recorta a los últimos `max_turnos` intercambios.
    """

    def __init__(
        self, *, url: str, client: Any | None = None, ttl: int = 3600, max_turnos: int = 12
    ) -> None:
        self._url = url
        self._client = client
        self._ttl = ttl
        self._max = max_turnos

    def _key(self, tenant_id: int, telefono: str) -> str:
        return f"wa:conv:{tenant_id}:{telefono}"

    async def cargar(self, tenant_id: int, telefono: str) -> list[Message]:
        cliente = self._client or _cliente_redis(self._url)
        dato = await cliente.get(self._key(tenant_id, telefono))
        if not dato:
            return []
        return [Message(role=m["role"], content=m["content"]) for m in json.loads(dato)]

    async def guardar(
        self, tenant_id: int, telefono: str, historial: list[Message], usuario: str, asistente: str
    ) -> None:
        cliente = self._client or _cliente_redis(self._url)
        turnos = [{"role": m.role, "content": m.content} for m in historial]
        turnos.append({"role": "user", "content": usuario})
        turnos.append({"role": "assistant", "content": asistente})
        recortado = turnos[-(2 * self._max):]
        await cliente.set(self._key(tenant_id, telefono), json.dumps(recortado), ex=self._ttl)

    async def anexar_usuario(self, tenant_id: int, telefono: str, texto: str) -> None:
        """Guarda un mensaje entrante SIN respuesta (durante el handoff humano): preserva el hilo.

        Mientras la conversación está en `humano` el agente no corre, pero el mensaje del cliente no se
        pierde: se anexa al historial para dar contexto cuando el bot reanude (y, más adelante, para la
        bandeja del dashboard).
        """
        cliente = self._client or _cliente_redis(self._url)
        dato = await cliente.get(self._key(tenant_id, telefono))
        turnos = json.loads(dato) if dato else []
        turnos.append({"role": "user", "content": texto})
        recortado = turnos[-(2 * self._max):]
        await cliente.set(self._key(tenant_id, telefono), json.dumps(recortado), ex=self._ttl)


# Tipos de los colaboradores inyectados en `AgenteWa` (resueltos por el composition root del worker).
AbrirTenant = Callable[[ResolvedTenant], AsyncIterator[AsyncSession]]
ResolverLLM = Callable[[int, Turno], Awaitable[LLMResuelto]]
Capacidades = Callable[[int], Awaitable[frozenset[str]]]


class AgenteWa:
    """Atiende un mensaje de WhatsApp con el agente de agenda y responde por Kapso.

    El turno es complejo (multi-paso con herramientas) → modelo orquestador (Sonnet), patrón del
    proyecto. La sesión del tenant se mantiene durante el bucle y se cierra al final: así la cita que
    agende el modelo se persiste (commit) antes de confirmarle al cliente.
    """

    def __init__(
        self,
        *,
        abrir_tenant: AbrirTenant,
        resolver_llm: ResolverLLM,
        capacidades: Capacidades,
        memoria: MemoriaWa,
        sender: KapsoSender,
        turno: Turno = Turno.ORQUESTADOR,
        gcal: CalendarPort | None = None,
    ) -> None:
        self._abrir_tenant = abrir_tenant
        self._resolver_llm = resolver_llm
        self._capacidades = capacidades
        self._memoria = memoria
        self._sender = sender
        self._turno = turno
        # Sync OPCIONAL con Google Calendar (write-only): se pasa al motor por turno. None = sin sync.
        self._gcal = gcal

    async def atender(self, mensaje: MensajeWa, tenant: ResolvedTenant) -> str:
        """Corre el bucle del agente y responde. Devuelve el texto enviado (para observabilidad/tests).

        Si la conversación del cliente está escalada a un humano (`estado=humano`), NO corre el agente:
        guarda el mensaje entrante y no responde, hasta que el negocio la resuelva (devuelva al bot).
        """
        texto = FALLBACK
        pausado = False
        try:
            capacidades = await self._capacidades(tenant.id)
            ctx = Contexto(
                tenant_id=tenant.id, usuario_id=0, rol="cliente", origen="whatsapp",
                cliente_telefono=mensaje.telefono, capacidades=capacidades,
            )
            historial: list[Message] = []
            async for session in self._abrir_tenant(tenant):
                conversaciones = ConversacionService(SqlConversacionRepository(session))
                # Pausa del agente: si está en manos de un humano, no se corre el LLM (regla del runtime).
                if await conversaciones.esta_en_humano(mensaje.telefono):
                    pausado = True
                    continue
                repo = SqlAgendaRepository(session)
                cfg = await repo.obtener_config()
                proveedor = await self._resolver_llm(tenant.id, self._turno)
                historial = await self._memoria.cargar(tenant.id, mensaje.telefono)
                deps = RuntimeDeps(
                    agenda=AgendaDeps(agenda=AgendaService(repo, gcal=self._gcal)),
                    handoff=HandoffDeps(conversaciones=conversaciones),
                    faq=FaqDeps(faq=FaqService(SqlConocimientoRepository(session))),
                )
                texto = await correr_bucle(
                    proveedor=proveedor,
                    system=construir_system(cfg.persona if cfg else None),
                    tools=exponer_runtime(ctx),       # agenda (gated por flag) + handoff (núcleo)
                    ctx=ctx, deps=deps, historial=historial, texto=mensaje.texto,
                )
                # commit al cerrar el generador → la cita agendada / el escalamiento quedan firmes.
            if pausado:
                # No se responde mientras lo atiende un humano; el entrante se preserva en el historial.
                await self._memoria.anexar_usuario(tenant.id, mensaje.telefono, mensaje.texto)
                log.info("wa_conversacion_en_humano_pausada", tenant_id=tenant.id)
                return ""
            await self._memoria.guardar(tenant.id, mensaje.telefono, historial, mensaje.texto, texto)
        except Exception:  # noqa: BLE001 — fallback elegante: nunca exponer el error interno al cliente
            log.exception("wa_agente_error", tenant_id=tenant.id)
            texto = FALLBACK
        await self._enviar(mensaje, texto)
        return texto

    async def _enviar(self, mensaje: MensajeWa, texto: str) -> None:
        try:
            await self._sender.enviar_texto(
                phone_number_id=mensaje.phone_number_id, to=mensaje.telefono,
                texto=whatsappify(texto),   # Markdown → formato que WhatsApp renderiza
            )
        except Exception:  # noqa: BLE001 — un fallo de envío no debe tumbar el job
            log.exception("wa_envio_error", tenant_id=None)


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
