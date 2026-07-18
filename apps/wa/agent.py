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
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ai.agenda_tools import AgendaDeps, ejecutar as agenda_ejecutar, exponer_catalogo as exponer_agenda
from ai.cobranza_tools import (
    CobranzaDeps,
    POR_NOMBRE as COBRANZA_POR_NOMBRE,
    ejecutar as cobranza_ejecutar,
    exponer_catalogo as exponer_cobranza,
)
from ai.cotizaciones_tools import (
    CotizacionesDeps,
    POR_NOMBRE as COTIZACIONES_POR_NOMBRE,
    ejecutar as cotizaciones_ejecutar,
    exponer_catalogo as exponer_cotizaciones,
)
from ai.envelope import Contexto, ErrorTool, Resultado
from ai.saneamiento import revisar as revisar_entrada
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
from ai.pedidos_tools import (
    PedidosDeps,
    POR_NOMBRE as PEDIDOS_POR_NOMBRE,
    ejecutar as pedidos_ejecutar,
    exponer_catalogo as exponer_pedidos,
)
from ai.postventa_tools import (
    PostventaDeps,
    POR_NOMBRE as POSTVENTA_POR_NOMBRE,
    ejecutar as postventa_ejecutar,
    exponer_catalogo as exponer_postventa,
)
from ai.reservas_tools import (
    ReservasDeps,
    POR_NOMBRE as RESERVAS_POR_NOMBRE,
    ejecutar as reservas_ejecutar,
    exponer_catalogo as exponer_reservas,
)
from apps.wa.kapso import KapsoSender, MensajeWa
from core.config.timezone import now_co
from core.llm.base import Message, ToolSpec
from core.llm.factory import LLMResuelto, Turno
from core.llm.gobierno import Gobierno
from core.logging import get_logger
from core.tenancy.context import ResolvedTenant
from modules.agenda.gcal import CalendarPort
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.service import AgendaService
from modules.cobranza.repository import SqlCobranzaRepository
from modules.cobranza.service import CobranzaService
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.service import ConversacionService
from modules.cotizaciones.repository import SqlCotizacionesRepository
from modules.cotizaciones.service import CotizacionesService
from core.pagos.ports import PagosPort
from modules.faq.repository import SqlConocimientoRepository
from modules.faq.service import FaqService
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.service import PagosService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import PedidosService
from modules.postventa.repository import SqlPostventaRepository
from modules.postventa.service import PostventaService
from modules.reservas.service import ReservasService

log = get_logger("wa.agent")

# Mensaje amable si el modelo o el canal fallan (nunca se expone el error interno).
FALLBACK = "Disculpa, tuve un problema para atenderte. ¿Puedes intentarlo de nuevo en un momento?"

# Mensaje fijo cuando el saneamiento bloquea el TEXTO entrante (inyección, texto desmesurado…):
# amable y genérico — sin detallar el motivo (no se le da retroalimentación al atacante).
RECHAZO_ENTRADA = "No puedo procesar ese mensaje. ¿Me dices en otras palabras en qué te ayudo?"

# Tope de iteraciones modelo↔herramientas por mensaje (agendar puede encadenar varias consultas).
_MAX_ITERS = 6

# El system prompt se COMPONE por pack activo (igual que el catálogo de herramientas): intro según
# el pack de dominio + secciones gateadas por capacidad + handoff siempre. Asistente ESPECIALIZADO
# (cumple la regla de Meta: nada de propósito general) con la persona/tono del negocio al final.
_INTRO_AGENDA = (
    "Eres un asistente virtual de citas por WhatsApp para un negocio de servicios. Tu ÚNICO propósito "
    "es ayudar al cliente a: ver los servicios, consultar horarios disponibles, y agendar, reagendar "
    "o cancelar SUS citas. No respondes temas ajenos a la agenda.\n"
    "Reglas: usa SIEMPRE las herramientas para consultar disponibilidad y para agendar/cambiar citas; "
    "nunca inventes horarios, precios ni confirmes una cita sin la herramienta. Pide los datos que "
    "falten (servicio, fecha/hora, nombre) antes de agendar. Responde en español, breve y cordial; "
    "las fechas y horas son de Colombia. Si te piden algo fuera de las citas, di con amabilidad que "
    "solo puedes ayudar con la agenda."
)

_INTRO_GENERICA = (
    "Eres un asistente virtual de atención al cliente por WhatsApp de un negocio. Tu ÚNICO propósito "
    "es atender los temas de ESTE negocio con las herramientas disponibles; no respondes temas ajenos. "
    "Nunca inventes datos, precios ni saldos: usa siempre las herramientas. Responde en español, "
    "breve y cordial; las fechas y horas son de Colombia."
)

_SECCION_FAQ = (
    "Para dudas generales del negocio (ubicación, horarios, precios, formas de pago, parqueo, "
    "políticas) usa responder_faq y responde SOLO con esa información. Si no hay información suficiente, "
    "NO inventes: ofrece pasar a un asesor humano (escalar_humano) o di que no tienes ese dato."
)

_SECCION_HANDOFF = (
    "Escala a un humano (escalar_humano) SOLO si en ESTE mensaje el cliente lo pide explícitamente o "
    "presenta una queja/tema fuera de tu alcance; nunca por algo dicho antes. Si el cliente solo saluda, "
    "salúdalo y ofrece tu ayuda — NUNCA des la bienvenida y escales en el mismo turno."
)

_SECCION_RECORDATORIO_CITA = (
    "Si el cliente responde a un RECORDATORIO de su cita: si confirma que asistirá (sí, confirmo, ahí "
    "estaré, dale) usa mis_citas para hallar su próxima cita y reconfírmala con reconfirmar_cita; si "
    "dice que no podrá o quiere cancelar, cancélala con cancelar_cita. Si quiere otro horario, reagenda."
)

# Sección de pedidos (solo con `pack_pedidos`, ADR 0016): el flujo armar → datos de entrega → confirmar.
_SECCION_PEDIDOS = (
    "Si el cliente quiere pedir comida/productos a domicilio: consulta SIEMPRE el catálogo con "
    "ver_menu (nunca inventes productos ni precios); arma el pedido con armar_pedido (puedes "
    "rearmarlo si cambia de opinión). Antes de confirmar recoge SIEMPRE los datos de entrega: "
    "NOMBRE del cliente, dirección EXACTA (calle y número, no solo el barrio), barrio, TELÉFONO de "
    "contacto real (el domiciliario lo llama al entregar) y método de pago — puedes pedirlos en un "
    "solo mensaje. SOLO con todos esos datos usa confirmar_pedido. Tras confirmar (y tras el pago "
    "si es transferencia), deja claro que el domiciliario se comunicará a ese teléfono para "
    "coordinar la entrega. Si pregunta por su pedido, usa estado_mi_pedido. Si un producto no "
    "aparece, ofrece las sugerencias de la herramienta o escala a un humano; jamás prometas algo "
    "fuera del menú."
)

# Sección de postventa (solo con `pack_postventa`, plan §2.6): la encuesta de seguimiento.
_SECCION_POSTVENTA = (
    "Si el cliente responde a la encuesta de seguimiento (un número del 1 al 5, 'excelente', "
    "'mala'…): registra su calificación con calificar_atencion (traduce palabras a 1-5; confirma "
    "si es ambiguo). Si la herramienta te da un link de reseña, invítalo con calidez a dejarla; "
    "si la calificación es baja, discúlpate y ofrece pasar con un humano."
)

# Sección de reservas (solo con `pack_reservas`, plan §2.7): noches sobre el motor de agenda.
_SECCION_RESERVAS = (
    "Si el huésped quiere reservar alojamiento: consulta disponibilidad SOLO con consultar_noches "
    "(fecha de llegada + noches; nunca inventes habitaciones, tarifas ni fechas libres) y reserva "
    "con reservar_habitacion cuando elija. Si la reserva queda pendiente de anticipo, explícalo con "
    "claridad y comparte el link de pago si la herramienta lo da. Para ver o cancelar su reserva usa "
    "mis_citas y cancelar_cita."
)

# Sección de cotizaciones (solo con `pack_ventas`, ADR 0017): el agente nunca inventa precio/stock.
_SECCION_COTIZACIONES = (
    "Si el cliente pregunta precios o disponibilidad ('¿a cómo…?', '¿tienes…?'): responde SOLO con "
    "cotizar_producto (aplica el precio real por cantidad; nunca inventes ni negocies precios o "
    "stock). Si quiere varios productos, arma la cotización con agregar_a_cotizacion (y "
    "quitar_de_cotizacion / ver_mi_cotizacion para ajustarla); cuando confirme que es todo, ciérrala "
    "con emitir_cotizacion y preséntale el detalle con su vigencia. Si un producto no aparece, "
    "ofrece las sugerencias de la herramienta o escala a un humano."
)

# Sección de cobranza (solo con `pack_cobranza`). El tono respetuoso es FIJO del sistema (ADR 0015):
# la `persona` del negocio no puede volverlo agresivo (esta sección manda sobre cualquier persona).
_SECCION_COBRANZA = (
    "Si el cliente escribe por su deuda o responde a un recordatorio de pago: consulta su saldo "
    "SOLO con mi_saldo (nunca lo calcules, inventes ni negocies); si promete pagar en una fecha, "
    "regístrala con prometer_pago; si dice que ya pagó, usa reportar_pago y pídele el comprobante "
    "(un asesor lo verificará — NUNCA confirmes tú que el pago quedó aplicado); si pide que no le "
    "escriban más recordatorios, usa no_mas_recordatorios y respétalo de inmediato. El tema del "
    "dinero se trata SIEMPRE con respeto y amabilidad: jamás presiones, amenaces ni avergüences al "
    "cliente, sin importar el tono que pida el negocio."
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


def construir_system(
    persona: str | None, capacidades: frozenset[str] | None = None, *, negocio: str | None = None
) -> str:
    """System prompt del asistente: intro + secciones por pack activo + ancla de fecha + persona.

    `capacidades=None` (llamadas legadas/tests) compone como si la empresa tuviera agenda + FAQ
    (el comportamiento histórico). En runtime el agente pasa las capacidades reales del tenant: una
    empresa SIN `pack_agenda` (p. ej. ferretería con solo cobranza) no se presenta como agente de citas.
    `negocio` (nombre comercial del tenant) ancla la identidad aunque no haya `persona` configurada.
    """
    if capacidades is None:
        capacidades = frozenset({"pack_agenda", "pack_faq"})
    partes = [_INTRO_AGENDA if "pack_agenda" in capacidades else _INTRO_GENERICA]
    if negocio:
        partes.insert(0, f"Eres el asistente virtual de {negocio}.")
    if "pack_reservas" in capacidades:
        partes.append(_SECCION_RESERVAS)
    if "pack_pedidos" in capacidades:
        partes.append(_SECCION_PEDIDOS)
    if "pack_ventas" in capacidades:
        partes.append(_SECCION_COTIZACIONES)
    if "pack_cobranza" in capacidades:
        partes.append(_SECCION_COBRANZA)
    if "pack_postventa" in capacidades:
        partes.append(_SECCION_POSTVENTA)
    if "pack_faq" in capacidades:
        partes.append(_SECCION_FAQ)
    partes.append(_SECCION_HANDOFF)
    if "pack_agenda" in capacidades:
        partes.append(_SECCION_RECORDATORIO_CITA)
    base = "\n".join(partes) + f"\n\n{_ancla_fecha()}"
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
    """Dependencias de TODOS los packs del runtime de cara al cliente (agenda + cobranza + transversales)."""

    agenda: AgendaDeps
    handoff: HandoffDeps
    faq: FaqDeps
    cobranza: CobranzaDeps
    pedidos: PedidosDeps
    cotizaciones: CotizacionesDeps
    reservas: ReservasDeps
    postventa: PostventaDeps


def exponer_runtime(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo: packs de dominio (gateados por flag) + transversales (handoff núcleo)."""
    return [
        *exponer_agenda(ctx), *exponer_reservas(ctx), *exponer_pedidos(ctx),
        *exponer_cotizaciones(ctx), *exponer_cobranza(ctx), *exponer_postventa(ctx),
        *exponer_faq(ctx), *exponer_handoff(ctx),
    ]


async def ejecutar_runtime(
    tool_call: Any, ctx: Contexto, deps: RuntimeDeps
) -> Resultado | ErrorTool:
    """Despacha la herramienta al pack que la define (transversales o packs de dominio).

    Saneamiento PRIMERO (ADR 0023): la misma malla ligera del dispatcher del bot interno
    (`ai.saneamiento`) corre aquí sobre los args crudos — el canal público es el más expuesto a
    inyección de instrucciones y valores absurdos. Bloqueado → `ErrorTool` sin tocar ningún pack.
    """
    motivo = revisar_entrada(tool_call.arguments)
    if motivo is not None:
        log.warning(
            "wa_saneamiento_bloqueo", tenant_id=ctx.tenant_id, tool=tool_call.name,
            motivo=motivo.detalle, recuperable=motivo.recuperable,
        )
        return ErrorTool("validacion", motivo.detalle, recuperable=motivo.recuperable)
    if tool_call.name in HANDOFF_POR_NOMBRE:
        return await handoff_ejecutar(tool_call, ctx, deps.handoff)
    if tool_call.name in FAQ_POR_NOMBRE:
        return await faq_ejecutar(tool_call, ctx, deps.faq)
    if tool_call.name in RESERVAS_POR_NOMBRE:
        return await reservas_ejecutar(tool_call, ctx, deps.reservas)
    if tool_call.name in PEDIDOS_POR_NOMBRE:
        return await pedidos_ejecutar(tool_call, ctx, deps.pedidos)
    if tool_call.name in COTIZACIONES_POR_NOMBRE:
        return await cotizaciones_ejecutar(tool_call, ctx, deps.cotizaciones)
    if tool_call.name in COBRANZA_POR_NOMBRE:
        return await cobranza_ejecutar(tool_call, ctx, deps.cobranza)
    if tool_call.name in POSTVENTA_POR_NOMBRE:
        return await postventa_ejecutar(tool_call, ctx, deps.postventa)
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
    perezoso e inyectable (tests): se crea al primer uso y se cachea en la instancia (el pool de
    redis-py multiplexa). Recorta a los últimos `max_turnos` intercambios.
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

    def _redis(self) -> Any:
        if self._client is None:
            self._client = _cliente_redis(self._url)
        return self._client

    async def cargar(self, tenant_id: int, telefono: str) -> list[Message]:
        cliente = self._redis()
        dato = await cliente.get(self._key(tenant_id, telefono))
        if not dato:
            return []
        return [Message(role=m["role"], content=m["content"]) for m in json.loads(dato)]

    async def guardar(
        self, tenant_id: int, telefono: str, historial: list[Message], usuario: str, asistente: str
    ) -> None:
        cliente = self._redis()
        turnos = [{"role": m.role, "content": m.content} for m in historial]
        turnos.append({"role": "user", "content": usuario})
        turnos.append({"role": "assistant", "content": asistente})
        recortado = turnos[-(2 * self._max):]
        await cliente.set(self._key(tenant_id, telefono), json.dumps(recortado), ex=self._ttl)

    async def limpiar(self, tenant_id: int, telefono: str) -> None:
        """Borra el historial del cliente (al RESOLVER el handoff): el bot retoma SIN el contexto viejo.

        Sin esto, al devolver la conversación al bot el LLM re-escalaría de inmediato por el historial
        previo (el cliente había pedido asesor + se llamó a escalar_humano).
        """
        cliente = self._redis()
        await cliente.delete(self._key(tenant_id, telefono))

    async def anexar_usuario(self, tenant_id: int, telefono: str, texto: str) -> None:
        """Guarda un mensaje entrante SIN respuesta (durante el handoff humano): preserva el hilo.

        Mientras la conversación está en `humano` el agente no corre, pero el mensaje del cliente no se
        pierde: se anexa al historial para dar contexto cuando el bot reanude (y, más adelante, para la
        bandeja del dashboard).
        """
        cliente = self._redis()
        dato = await cliente.get(self._key(tenant_id, telefono))
        turnos = json.loads(dato) if dato else []
        turnos.append({"role": "user", "content": texto})
        recortado = turnos[-(2 * self._max):]
        await cliente.set(self._key(tenant_id, telefono), json.dumps(recortado), ex=self._ttl)


# Tipos de los colaboradores inyectados en `AgenteWa` (resueltos por el composition root del worker).
AbrirTenant = Callable[[ResolvedTenant], AsyncIterator[AsyncSession]]
ResolverLLM = Callable[[int, Turno], Awaitable[LLMResuelto]]
Capacidades = Callable[[int], Awaitable[frozenset[str]]]
# PSP del frente de pagos (ADR 0013) por tenant: None = sin PSP (modo manual). Opcional.
ResolverPsp = Callable[[int], Awaitable[PagosPort | None]]


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
        resolver_psp: ResolverPsp | None = None,
        gobierno: Gobierno | None = None,
    ) -> None:
        self._abrir_tenant = abrir_tenant
        self._resolver_llm = resolver_llm
        self._capacidades = capacidades
        self._memoria = memoria
        self._sender = sender
        self._turno = turno
        # Sync OPCIONAL con Google Calendar (write-only): se pasa al motor por turno. None = sin sync.
        self._gcal = gcal
        # PSP OPCIONAL por tenant (ADR 0013): habilita el link de cobro al confirmar un pedido.
        self._resolver_psp = resolver_psp
        # Gobierno OPCIONAL (ADR 0024): rate-limit + presupuesto por empresa ANTES de correr el LLM.
        self._gobierno = gobierno

    async def atender(self, mensaje: MensajeWa, tenant: ResolvedTenant) -> str:
        """Corre el bucle del agente y responde. Devuelve el texto enviado (para observabilidad/tests).

        Si la conversación del cliente está escalada a un humano (`estado=humano`), NO corre el agente:
        guarda el mensaje entrante y no responde, hasta que el negocio la resuelva (devuelva al bot).
        """
        texto = FALLBACK
        pausado = False
        bloqueado = False
        cortado = False
        try:
            capacidades = await self._capacidades(tenant.id)
            ctx = Contexto(
                tenant_id=tenant.id, usuario_id=0, rol="cliente", origen="whatsapp",
                cliente_telefono=mensaje.telefono, capacidades=capacidades,
            )
            historial: list[Message] = []
            # `aclosing`: si algo lanza DENTRO del bloque (p. ej. resolver_llm), el generador de sesión
            # se cierra YA (rollback + conexión devuelta), no queda suspendido en `yield` sosteniendo
            # una conexión asyncpg que el GC finalizaría tarde y colgaría el cierre del event loop.
            async with aclosing(self._abrir_tenant(tenant)) as sesiones:
                async for session in sesiones:
                    repo_conv = SqlConversacionRepository(session)
                    conversaciones = ConversacionService(repo_conv)
                    # Persiste el HILO del inbox: asegura la fila de la conversación (para que aparezca en
                    # el inbox aunque el bot la resuelva) y guarda el mensaje entrante del cliente. El
                    # commit al cerrar el generador lo hace firme y entrega su evento SSE.
                    await repo_conv.asegurar(mensaje.telefono)
                    await repo_conv.agregar_mensaje(
                        mensaje.telefono, "entrante", "cliente", mensaje.texto
                    )
                    # Pausa del agente: si está en manos de un humano, no se corre el LLM (regla del runtime).
                    if await conversaciones.esta_en_humano(mensaje.telefono):
                        pausado = True
                        continue
                    # Saneamiento del TEXTO entrante (ADR 0023): la inyección llega por el mensaje,
                    # no solo por args de tools. Bloqueado → respuesta fija amable SIN correr el LLM
                    # y sin envenenar la memoria; el hilo del inbox sí registra ambos lados.
                    motivo = revisar_entrada({"texto": mensaje.texto})
                    if motivo is not None:
                        bloqueado = True
                        texto = RECHAZO_ENTRADA
                        log.warning(
                            "wa_texto_bloqueado", tenant_id=tenant.id,
                            motivo=motivo.detalle, recuperable=motivo.recuperable,
                        )
                        await repo_conv.agregar_mensaje(mensaje.telefono, "saliente", "bot", texto)
                        continue
                    # Gobierno de agentes (ADR 0024): rate-limit + presupuesto ANTES de correr el LLM.
                    # Cortado → respuesta amable fija SIN llamar al modelo; se registra en el inbox y no
                    # se escribe la memoria (el corte no es parte del hilo de la conversación).
                    if self._gobierno is not None:
                        decision = await self._gobierno.evaluar(tenant.id, mensaje.telefono)
                        if not decision.permitido:
                            cortado = True
                            texto = decision.mensaje or FALLBACK
                            log.info(
                                "wa_gobierno_cortado", tenant_id=tenant.id,
                                corte=decision.corte.value if decision.corte else None,
                            )
                            await repo_conv.agregar_mensaje(mensaje.telefono, "saliente", "bot", texto)
                            continue
                    repo = SqlAgendaRepository(session)
                    cfg = await repo.obtener_config()
                    proveedor = await self._resolver_llm(tenant.id, self._turno)
                    historial = await self._memoria.cargar(tenant.id, mensaje.telefono)
                    deps = RuntimeDeps(
                        agenda=AgendaDeps(agenda=AgendaService(repo, gcal=self._gcal)),
                        handoff=HandoffDeps(conversaciones=conversaciones),
                        faq=FaqDeps(faq=FaqService(SqlConocimientoRepository(session))),
                        cobranza=CobranzaDeps(
                            cobranza=CobranzaService(SqlCobranzaRepository(session)),
                            conversaciones=conversaciones,
                        ),
                        pedidos=PedidosDeps(
                            pedidos=PedidosService(SqlPedidosRepository(session)),
                            pagos=await self._pagos(tenant, session, capacidades),
                        ),
                        cotizaciones=CotizacionesDeps(
                            cotizaciones=CotizacionesService(SqlCotizacionesRepository(session)),
                        ),
                        reservas=ReservasDeps(
                            reservas=ReservasService(repo),
                            pagos=await self._pagos(tenant, session, capacidades),
                        ),
                        postventa=PostventaDeps(
                            postventa=PostventaService(SqlPostventaRepository(session)),
                        ),
                    )
                    texto = await correr_bucle(
                        proveedor=proveedor,
                        system=construir_system(
                            cfg.persona if cfg else None, capacidades, negocio=tenant.nombre,
                        ),
                        tools=exponer_runtime(ctx),       # agenda (gated por flag) + handoff (núcleo)
                        ctx=ctx, deps=deps, historial=historial, texto=mensaje.texto,
                    )
                    # Hilo del inbox: guarda la respuesta del bot (saliente). Si el agente escaló en este
                    # turno, igual queda el texto que el bot alcanzó a dar antes de pasar al humano.
                    await repo_conv.agregar_mensaje(mensaje.telefono, "saliente", "bot", texto)
                    # commit al cerrar el generador → la cita agendada / el escalamiento quedan firmes.
            if pausado:
                # No se responde mientras lo atiende un humano; el entrante se preserva en el historial.
                await self._memoria.anexar_usuario(tenant.id, mensaje.telefono, mensaje.texto)
                log.info("wa_conversacion_en_humano_pausada", tenant_id=tenant.id)
                return ""
            if not bloqueado and not cortado:
                await self._memoria.guardar(
                    tenant.id, mensaje.telefono, historial, mensaje.texto, texto
                )
        except Exception:  # noqa: BLE001 — fallback elegante: nunca exponer el error interno al cliente
            log.exception("wa_agente_error", tenant_id=tenant.id)
            texto = FALLBACK
        await self._enviar(mensaje, texto, tenant.id)
        return texto

    async def _pagos(
        self, tenant: ResolvedTenant, session: AsyncSession, capacidades: frozenset[str]
    ) -> PagosService | None:
        """`PagosService` del turno SOLO si el tenant tiene `pagos_online` (con su PSP si hay llave).

        Sin la capacidad, None: las herramientas no crean cobros. El resolver de PSP es opcional
        (tests/entornos sin pagos); sin él, el servicio nace en modo manual (cobro sin link).
        """
        if "pagos_online" not in capacidades:
            return None
        psp = await self._resolver_psp(tenant.id) if self._resolver_psp is not None else None
        return PagosService(SqlPagosRepository(session), psp=psp)

    async def _enviar(self, mensaje: MensajeWa, texto: str, tenant_id: int) -> None:
        try:
            await self._sender.enviar_texto(
                phone_number_id=mensaje.phone_number_id, to=mensaje.telefono,
                texto=whatsappify(texto),   # Markdown → formato que WhatsApp renderiza
            )
        except Exception:  # noqa: BLE001 — un fallo de envío no debe tumbar el job
            log.exception("wa_envio_error", tenant_id=tenant_id)


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
