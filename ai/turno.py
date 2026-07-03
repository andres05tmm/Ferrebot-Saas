"""Orquestador del turno del bot: contexto RAG + bucle del agente + persistencia (entregable 4).

Es el `TurnoHandler` real que el webhook (entregable 1) inyecta como `deps.procesar`. Se expone como
una **factory** que captura el `Dispatcher` y los *stores* (memoria, costos, recursos) y devuelve un
handler con la firma de `apps.bot.ports.TurnoHandler`:

    TurnoHandler = Callable[[UpdateBot, Contexto, AsyncSession, Notificador], Awaitable[None]]

Flujo de un turno (GREEN):
  1. cargar historial reciente (best-effort) por la sesión del tenant que llega al handler;
  2. armar el system prompt mínimo (persona + fecha Colombia + entidades recordadas, solo LECTURA);
  3. construir un `Recursos` **nuevo por turno** (nunca compartido; ai.dispatcher.Recursos);
  4. resolver el proveedor (`dispatcher.seleccionar_proveedor`) y **envolverlo con `ProveedorMedido`**
     (token accounting por el borde del proveedor) ANTES de correr `ai.agent.ejecutar_turno`;
  5. responder por el `Notificador`;
  6. persistir el turno (user + assistant) — best-effort.

Token accounting: NO se cuenta en `ai.agent` (varios puntos de salida + 2 generaciones). Se cuenta en
`core.llm.medicion.ProveedorMedido`, que acumula los tokens de cada `generate` en el `CostosStore`
(SqlCostosRepository → `api_costo_diario`). El handler solo cablea el wrapper.

Scratch del turno (sin tabla ni store nuevo): el `Recursos` fresco del paso 3 + la confirmación R3
en Redis (entregable 3). El webhook es *stateless*: cada turno relee el historial de PG; no hay caché
en proceso. SQL solo en `modules/memoria/repository.py`; aquí cero SQL.

Respaldo (cierra la deuda de NOTAS-ENTREGABLES E2): un fallo del proveedor (timeout/credencial/5xx)
se traduce en un mensaje amable al usuario por el `Notificador`; nunca una excepción que escale a 500
ni silencio.

FOLLOW-UP (NO en E4): el **writer** de `memoria_entidades` (recordar último cliente/producto al final
del turno) queda diferido. Su fuente correcta es `Resultado.data`, que el handler no ve hoy
(`RespuestaAgente` no transporta `data`); estandarizar el mapeo tool→entidad + exponer `data` es un
mini-spec aparte. En E4 el system prompt solo CONSUME `leer_entidades` (lectura); `recordar_entidad`
existe y está probado a nivel de servicio, pero el turno aún no lo invoca. Ver NOTAS-ENTREGABLES.md.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ai.bypass import VentaPreparada

from sqlalchemy.ext.asyncio import AsyncSession

from ai.agent import RespuestaAgente, ejecutar_turno, texto_de_respuesta
from ai.confirmacion import ConfirmStore, VentaPendienteStore, es_afirmacion, es_negacion
from ai.dispatcher import Dispatcher, Recursos, Respuesta
from ai.envelope import Contexto
from ai.rieles import Confirmar
from apps.bot.ports import (
    ArchivosTelegram,
    CallbackBot,
    CallbackHandler,
    Notificador,
    RecursosBot,
    TurnoHandler,
    UpdateBot,
)
from core.config.timezone import today_co
from core.llm.factory import LLMResuelto, Turno
from core.llm.gobierno import Gobierno
from core.llm.medicion import CostosStore, ProveedorMedido
from core.logging import get_logger
from core.voz.filtros import es_transcripcion_silencio
from core.voz.transcriptor import Transcriptor
from modules.memoria.service import (
    TIPO_ULTIMO_CLIENTE,
    TIPO_ULTIMO_PRODUCTO,
    AudioLogsRepo,
    MemoriaService,
)

log = get_logger("ai.turno")

# Mensaje al usuario ante un fallo no recuperable del proveedor (nunca 500 ni silencio).
MENSAJE_RESPALDO = (
    "Tuve un problema para procesar tu mensaje. Inténtalo de nuevo en un momento, por favor."
)
# Voz sin la capacidad habilitada / transcripción ininteligible (silencio/ruido/alucinación).
MENSAJE_VOZ_DESHABILITADA = "El registro por voz no está habilitado para tu empresa."
MENSAJE_NO_ENTENDI = "No te entendí, ¿puedes repetirlo?"
# Capacidad requerida para procesar notas de voz (feature-flags.md; además de bot_telegram).
CAP_VENTAS_VOZ = "ventas_voz"
# Respuesta cuando el usuario niega una mutación pendiente de confirmación.
MENSAJE_CANCELADO = "Listo, cancelado."
# Respuesta cuando se cancela una venta pendiente de método de pago (botón [Cancelar]).
MENSAJE_VENTA_CANCELADA = "Venta cancelada."
# Respuesta cuando el botón se pulsa pero ya no hay pendiente (expiró el TTL o doble-tap ya resuelto).
MENSAJE_VENTA_EXPIRADA = "Esa venta ya no está disponible. Vuelve a escribirla, por favor."

# Botonera de método de pago para una venta del bypass: fila de métodos + fila [Cancelar].
# Cada botón es (texto visible, callback_data); el handler enruta por el callback_data.
PREFIJO_PAGO = "pago:"                 # pago:<metodo> → fija metodo_pago y ejecuta
CALLBACK_CANCELAR = "venta:cancelar"   # descarta el pendiente
BOTONES_METODO_PAGO: tuple[tuple[str, str], ...] = (
    ("Efectivo", f"{PREFIJO_PAGO}efectivo"),
    ("Transferencia", f"{PREFIJO_PAGO}transferencia"),
    ("Datáfono", f"{PREFIJO_PAGO}datafono"),
)
TECLADO_METODO_PAGO: tuple[tuple[tuple[str, str], ...], ...] = (
    BOTONES_METODO_PAGO,
    (("Cancelar", CALLBACK_CANCELAR),),
)

# Factories atadas a la sesión del tenant que el handler recibe en cada turno.
MemoriaFactory = Callable[[AsyncSession], MemoriaService]
CostosFactory = Callable[[AsyncSession], CostosStore]
RecursosFactory = Callable[[AsyncSession], Recursos]
AudioFactory = Callable[[AsyncSession], AudioLogsRepo]
# El bucle del agente, inyectable para pruebas (default: el real).
EjecutarTurno = Callable[..., Awaitable[RespuestaAgente]]


# Bypass (Paso A: ventas): camino rápido sin IA. `intentar` devuelve una Respuesta del despachador
# (Resultado/ErrorTool/Preguntar/Confirmar) o None = CaeAlModelo (el turno sigue al modelo).
class BypassPort(Protocol):
    async def intentar(self, texto: str, ctx: Contexto, recursos: Recursos) -> Respuesta | None: ...
    # Variante con botones: hace el match pero NO ejecuta; devuelve la venta lista o None (no-match).
    async def preparar(
        self, texto: str, ctx: Contexto, recursos: Recursos
    ) -> "VentaPreparada | None": ...


# Factory por turno: ata el catálogo (capa exacta) a la sesión del tenant; None = sin bypass.
BypassFactory = Callable[[AsyncSession], BypassPort | None]


# Reglas de dominio FERRETERO del prompt (nombre base con ejemplos del oficio, fracciones no
# proporcionales, stock informal, lija, vinilos). Solo entran cuando el rubro es ferretería (o no
# hay rubro configurado: fallback histórico — Punto Rojo no cambia ni un byte).
_LINEAS_FERRETERIA = [
    "Cuando consultes o registres un producto, búscalo por su nombre base (el sustantivo "
    "principal), sin cantidades, fracciones ni unidades de empaque: de 'medio galón de thinner' "
    "busca 'thinner'; de 'galón de esmalte blanco' busca 'esmalte blanco'. Si no aparece, "
    "reintenta con un término más corto o más general antes de rendirte.",
    "Para saber cuánto cuesta un producto, una cantidad o una fracción, usa SIEMPRE la "
    "herramienta consultar_producto. NUNCA calcules el valor de una fracción dividiendo el del "
    "galón o de la unidad entera: las fracciones no valen proporcional (un 1/2 no es la mitad "
    "del entero). Si la herramienta no devuelve un valor para la fracción que piden, dilo "
    "claramente y pregunta cuánto cobrar, en vez de inventarlo.",
    "El inventario en cero o negativo NO impide registrar una venta: este negocio es informal y "
    "vende aunque el conteo marque cero. Registra la venta de todas formas.",
    "Lija: la palabra 'esmeril' decide el producto. 'lija N' (sin 'esmeril') es lija normal, se "
    "vende por hoja/unidad. 'lija esmeril N' es otro producto y se vende por CENTÍMETRO: el "
    "cliente pide los cm. Necesitas SIEMPRE el número de grano (N°36, 60, 80 o 100) y, para la "
    "esmeril, también los cm. Si falta el número o los cm, PREGÚNTALO (¿N°36, 60, 80 o 100? / "
    "¿cuántos centímetros?); nunca registres una lija ni calcules su valor a mano sin esos datos: "
    "usa consultar_producto.",
    "Vinilos y cuñetes: cuánto valen depende del TIPO (Tipo 1/2/3, abreviado T1/T2/T3 o solo "
    "'t'), NO del color. Para saber su valor, consulta por el tipo (p. ej. consultar_producto "
    "con 'vinilo t1') y responde el valor del tipo SIN enumerar colores. Si no sabes el tipo, "
    "pregunta '¿Tipo 1, 2 o 3?', nunca por el color. El color solo importa al registrar la venta "
    "(para descontar el inventario).",
]


def _es_ferreteria(rubro: str | None) -> bool:
    """None (sin configurar) o el rubro ferretero explícito → prompt ferretero histórico."""
    if rubro is None:
        return True
    plano = rubro.strip().lower().replace("í", "i")
    return plano in ("ferreteria", "ferretería")


def construir_system_prompt(
    entidades: dict[str, dict], *, rubro: str | None = None, hoy: date | None = None
) -> str:
    """System prompt mínimo (helper puro): persona por RUBRO del negocio (config_empresa) + fecha
    de HOY en zona Colombia + bloque "Contexto reciente" con las entidades recordadas (si existen).

    `rubro=None` → prompt ferretero EXACTO de siempre (fallback: los tenants sin rubro configurado
    no cambian). Con otro rubro, la intro se parametriza y las reglas ferreteras no entran.
    PROHIBIDO meter el inventario o los valores monetarios del negocio: el modelo los obtiene
    por herramientas (tool-calling), no por el prompt.
    """
    hoy = hoy or today_co()
    ferreteria = _es_ferreteria(rubro)
    intro = (
        "Eres el asistente de ventas de una ferretería. Atiendes en español, de forma breve y "
        "concreta, y ayudas a registrar ventas, gastos, fiados y consultas del negocio."
        if ferreteria
        else f"Eres el asistente de operación de una {rubro.strip()}. Atiendes en español, de forma "
        "breve y concreta, y ayudas al equipo a registrar ventas, gastos y consultas del negocio."
    )
    lineas = [
        intro,
        f"Fecha de hoy (Colombia): {hoy.isoformat()}.",
        "Para cualquier dato del negocio usa las herramientas disponibles; nunca inventes valores.",
        "Escribe siempre en texto plano para Telegram, con tono profesional y cordial. No uses "
        "Markdown ni símbolos de formato: nada de asteriscos, guiones bajos, almohadillas ni "
        "viñetas. Si necesitas enumerar opciones, sepáralas en renglones con frases completas. "
        "Usa emojis con moderación, solo si aportan claridad.",
    ]
    if ferreteria:
        lineas.extend(_LINEAS_FERRETERIA)
    else:
        lineas.append(
            "Cuando consultes o registres un producto o servicio, búscalo por su nombre base, sin "
            "cantidades ni unidades. Para saber cuánto cuesta, usa SIEMPRE la herramienta "
            "consultar_producto; si no devuelve un valor, dilo claramente y pregunta cuánto cobrar, "
            "en vez de inventarlo.",
        )
    bloque = _bloque_contexto(entidades)
    if bloque:
        lineas.append(bloque)
    return "\n".join(lineas)


def _bloque_contexto(entidades: dict[str, dict]) -> str:
    """Bloque "Contexto reciente" con el último cliente/producto. "" si no hay entidades útiles."""
    partes: list[str] = []
    cliente = entidades.get(TIPO_ULTIMO_CLIENTE)
    if cliente:
        partes.append(f"- Último cliente mencionado: {cliente.get('nombre')} (id {cliente.get('id')}).")
    producto = entidades.get(TIPO_ULTIMO_PRODUCTO)
    if producto:
        partes.append(f"- Último producto mencionado: {producto.get('nombre')} (id {producto.get('id')}).")
    if not partes:
        return ""
    return "Contexto reciente:\n" + "\n".join(partes)


async def _resolver_texto_voz(
    update: UpdateBot,
    ctx: Contexto,
    notificador: Notificador,
    *,
    transcriptor: Transcriptor | None,
    archivos: ArchivosTelegram | None,
    audios: "AudioFactory | None",
    session: AsyncSession,
) -> str | None:
    """Convierte una nota de voz en texto para el pipeline. None = ya se respondió, el turno termina.

    Orden: capacidad → descargar+transcribir (fallo → MENSAJE_RESPALDO) → filtrar silencio
    (→ MENSAJE_NO_ENTENDI) → bitácora best-effort. El fallo de voz nunca llega a `ejecutar_turno`.
    """
    if not ctx.tiene_capacidad(CAP_VENTAS_VOZ):
        await notificador.responder(update.chat_id, MENSAJE_VOZ_DESHABILITADA)
        return None
    try:
        audio = await archivos.descargar(update.voz_file_id)   # type: ignore[union-attr]
        transcripcion = await transcriptor.transcribir(audio, prompt=None)  # type: ignore[union-attr]
    except Exception:
        log.warning("voz_descarga_o_transcripcion_fallo", chat_id=update.chat_id, exc_info=True)
        await notificador.responder(update.chat_id, MENSAJE_RESPALDO)
        return None
    if es_transcripcion_silencio(transcripcion.texto, transcripcion.segmentos):
        await notificador.responder(update.chat_id, MENSAJE_NO_ENTENDI)
        return None
    if audios is not None:
        try:
            await audios(session).registrar(update.chat_id, transcripcion.texto, None)
        except Exception:
            log.warning("voz_audio_logs_fallo", chat_id=update.chat_id, exc_info=True)
    return transcripcion.texto


async def _manejar_confirmacion(
    update: UpdateBot,
    ctx: Contexto,
    session: AsyncSession,
    notificador: Notificador,
    texto: str | None,
    *,
    confirm: ConfirmStore,
    dispatcher: Dispatcher,
    crear_recursos: RecursosFactory,
    memoria: MemoriaFactory,
) -> bool:
    """Resuelve un pendiente de confirmación. True = turno resuelto (el handler hace return).

    Afirmación → re-despacha el `tool_call` guardado (confirmado=True, MISMA key), sin modelo.
    Negación → cancela. Otro → descarta el pendiente y deja seguir el turno normal (False).
    """
    pendiente = await confirm.obtener(ctx.tenant_id, update.chat_id)
    if pendiente is None:
        return False
    await confirm.borrar(ctx.tenant_id, update.chat_id)   # el pendiente se consume en cualquier caso
    if es_afirmacion(texto or ""):
        ctx2 = replace(ctx, confirmado=True, idempotency_key=pendiente.idempotency_key)
        try:
            resultado = await dispatcher.ejecutar(pendiente.tool_call, ctx2, crear_recursos(session))
        except Exception:
            log.warning("confirmacion_redespacho_fallo", chat_id=update.chat_id, exc_info=True)
            await notificador.responder(update.chat_id, MENSAJE_RESPALDO)
            return True
        respuesta = texto_de_respuesta(resultado)
        await notificador.responder(update.chat_id, respuesta)
        try:
            await memoria(session).guardar_turno(update.chat_id, usuario=texto or "", asistente=respuesta)
        except Exception:
            log.warning("turno_persistencia_fallo", chat_id=update.chat_id, exc_info=True)
        return True
    if es_negacion(texto or ""):
        await notificador.responder(update.chat_id, MENSAJE_CANCELADO)
        return True
    return False                                          # comando nuevo → turno normal


async def _resolver_por_bypass(
    crear_bypass: BypassFactory,
    *,
    texto: str,
    ctx: Contexto,
    session: AsyncSession,
    recursos_turno: Recursos,
    notificador: Notificador,
    memoria_svc: MemoriaService,
    chat_id: int,
    pendientes: VentaPendienteStore | None = None,
) -> bool:
    """Intenta resolver el turno por el bypass (camino rápido sin IA). True = resuelto (return).

    Resiliencia: un fallo del bypass NUNCA tumba el turno (devuelve False → cae al modelo).

    Dos modos:
      - con `pendientes` (flujo de botones): NO ejecuta la venta — `_ofrecer_metodo_pago` prepara la
        venta, guarda el pendiente y manda el resumen + botonera de método de pago;
      - sin `pendientes` (flujo clásico): el bypass ejecuta y se relaya el resultado del despachador
        (`texto_de_respuesta`), persistiendo el turno (best-effort).
    """
    if pendientes is not None:
        try:
            bypass = crear_bypass(session)
        except Exception:
            log.warning("bypass_fallo", chat_id=chat_id, exc_info=True)
            return False
        if bypass is None:
            return False
        return await _ofrecer_metodo_pago(
            bypass, texto=texto, ctx=ctx, recursos_turno=recursos_turno,
            notificador=notificador, pendientes=pendientes, chat_id=chat_id,
        )

    try:
        bypass = crear_bypass(session)
        r = await bypass.intentar(texto, ctx, recursos_turno) if bypass is not None else None
    except Exception:
        log.warning("bypass_fallo", chat_id=chat_id, exc_info=True)
        return False
    if r is None:
        return False
    texto_r = texto_de_respuesta(r)
    await notificador.responder(chat_id, texto_r)
    log.info(
        "bypass_resuelto", chat_id=chat_id,
        evento=getattr(r, "evento", None), idempotente=getattr(r, "idempotente", None),
    )
    try:
        await memoria_svc.guardar_turno(chat_id, usuario=texto or "", asistente=texto_r)
    except Exception:
        log.warning("turno_persistencia_fallo", chat_id=chat_id, exc_info=True)
    return True


async def _ofrecer_metodo_pago(
    bypass: BypassPort,
    *,
    texto: str,
    ctx: Contexto,
    recursos_turno: Recursos,
    notificador: Notificador,
    pendientes: VentaPendienteStore,
    chat_id: int,
) -> bool:
    """Prepara la venta (sin registrarla), guarda el pendiente y ofrece método de pago con botones.

    True = el bypass resolvió (se mostró la botonera); False = no-match (cae al modelo).

    `bypass.preparar` → si hay venta: guarda el pendiente (ToolCall SIN `metodo_pago` + la
    `ctx.idempotency_key` ESTABLE del mensaje, que el callback reusará para no duplicar) y manda el
    resumen + `TECLADO_METODO_PAGO`. NADA se ejecuta hasta que el usuario pulse un botón.
    """
    try:
        preparada = await bypass.preparar(texto, ctx, recursos_turno)
    except Exception:
        log.warning("bypass_fallo", chat_id=chat_id, exc_info=True)
        return False
    if preparada is None:
        return False                              # no-match → el turno cae al modelo
    try:
        await pendientes.guardar(
            ctx.tenant_id, chat_id,
            tool_call=preparada.tool_call, idempotency_key=ctx.idempotency_key or "",
        )
    except Exception:
        # Si no podemos guardar el pendiente, no ofrecemos botones huérfanos: cae al modelo.
        log.warning("venta_pendiente_guardar_fallo", chat_id=chat_id, exc_info=True)
        return False
    await notificador.responder(chat_id, preparada.resumen, teclado=TECLADO_METODO_PAGO)
    log.info("bypass_pendiente_pago", chat_id=chat_id)
    return True


def crear_turno_handler(
    *,
    dispatcher: Dispatcher,
    memoria: MemoriaFactory,
    costos: CostosFactory,
    crear_recursos: RecursosFactory,
    ejecutar: EjecutarTurno = ejecutar_turno,
    turno: Turno = Turno.WORKER,
    recursos: RecursosBot | None = None,
    audios: AudioFactory | None = None,
    confirm: ConfirmStore | None = None,
    crear_bypass: BypassFactory | None = None,
    pendientes: VentaPendienteStore | None = None,
    gobierno: Gobierno | None = None,
) -> TurnoHandler:
    """Captura el dispatcher + stores (+ voz, opcional) y devuelve el `TurnoHandler` del webhook.

    `recursos` (la caché `RecursosBot` por empresa) y `audios` son OPCIONALES: un despliegue sin voz
    deja el pipeline de texto intacto. Con voz, un update con `voz_file_id` resuelve el transcriptor
    y los archivos de ESA empresa (`recursos.para(ctx.tenant_id)`) y se transcribe ANTES del pipeline;
    el texto resultante entra como si el usuario lo hubiera escrito (E5). Sin `recursos`, un update de
    voz responde "voz no disponible" (no hay con qué transcribirla).
    """

    async def handler(
        update: UpdateBot, ctx: Contexto, session: AsyncSession, notificador: Notificador
    ) -> None:
        # Voz: se resuelve a texto ANTES del pipeline; None = ya se respondió (capacidad/fallo/silencio).
        if update.voz_file_id:
            if recursos is None:
                await notificador.responder(update.chat_id, MENSAJE_VOZ_DESHABILITADA)
                return
            bundle = await recursos.para(ctx.tenant_id)   # transcriptor/archivos de la empresa
            texto = await _resolver_texto_voz(
                update, ctx, notificador,
                transcriptor=bundle.transcriptor, archivos=bundle.archivos,
                audios=audios, session=session,
            )
            if texto is None:
                return
        else:
            texto = update.texto

        # Confirmación entre turnos (re-despacho determinista): resolver el pendiente ANTES del turno.
        if confirm is not None:
            if await _manejar_confirmacion(
                update, ctx, session, notificador, texto,
                confirm=confirm, dispatcher=dispatcher, crear_recursos=crear_recursos, memoria=memoria,
            ):
                return

        memoria_svc = memoria(session)
        historial = await memoria_svc.cargar_historial(update.chat_id)        # best-effort
        entidades = await memoria_svc.leer_entidades(update.chat_id)          # best-effort
        system = construir_system_prompt(entidades, rubro=ctx.rubro)
        recursos_turno = crear_recursos(session)                              # fresco por turno

        # Bypass (Paso A: ventas): camino rápido sin IA, ANTES del modelo. None/False = CaeAlModelo.
        # Con `pendientes`, el bypass NO ejecuta: ofrece método de pago con botones (R3-bot).
        # Gate por capacidad (ADR 0021): el bypass REGISTRA ventas → sin la feature `ventas` (o su
        # meta-pack `pos`) queda inerte y el turno cae al modelo, que tampoco tendrá esa tool.
        if crear_bypass is not None and texto and ctx.tiene_capacidad("ventas") and await _resolver_por_bypass(
            crear_bypass, texto=texto, ctx=ctx, session=session, recursos_turno=recursos_turno,
            notificador=notificador, memoria_svc=memoria_svc, chat_id=update.chat_id,
            pendientes=pendientes,
        ):
            return

        # Gobierno de agentes (ADR 0024): rate-limit + presupuesto ANTES de gastar la llamada al modelo.
        # El bypass (arriba) no pasa por aquí: es determinista y barato. Cortado → mensaje amable y fin.
        if gobierno is not None:
            decision = await gobierno.evaluar(ctx.tenant_id, update.chat_id)
            if not decision.permitido:
                log.info(
                    "gobierno_turno_cortado", chat_id=update.chat_id,
                    corte=decision.corte.value if decision.corte else None,
                )
                await notificador.responder(update.chat_id, decision.mensaje or MENSAJE_RESPALDO)
                return

        try:
            base = await dispatcher.seleccionar_proveedor(ctx.tenant_id, turno=turno)
            proveedor = LLMResuelto(
                provider=ProveedorMedido(base.provider, costos(session)),     # token accounting
                model=base.model,
                provider_nombre=base.provider_nombre,
            )
            respuesta = await ejecutar(
                texto=texto, ctx=ctx, ejecutor=dispatcher, recursos=recursos_turno,
                proveedor=proveedor, historial=historial, system=system,
            )
        except Exception:
            log.warning("turno_fallo_proveedor", chat_id=update.chat_id, exc_info=True)
            await notificador.responder(update.chat_id, MENSAJE_RESPALDO)
            return

        # CR-2: si el turno pidió confirmación, guardar el pendiente (best-effort) antes de responder.
        if confirm is not None and respuesta.confirmacion_pendiente is not None:
            try:
                await confirm.guardar(
                    ctx.tenant_id, update.chat_id,
                    tool_call=respuesta.confirmacion_pendiente, idempotency_key=ctx.idempotency_key,
                )
            except Exception:
                log.warning("confirmacion_guardar_fallo", chat_id=update.chat_id, exc_info=True)

        await notificador.responder(update.chat_id, respuesta.texto)
        # Persistencia best-effort: jamás degrada la respuesta ya enviada (try propio, sin respaldo).
        try:
            await memoria_svc.guardar_turno(
                update.chat_id, usuario=texto or "", asistente=respuesta.texto
            )
        except Exception:
            log.warning("turno_persistencia_fallo", chat_id=update.chat_id, exc_info=True)
        # Writer de memoria_entidades (ADR 0024): recuerda la última entidad mencionada (cliente/
        # producto) a partir de la `data` del Resultado. Best-effort: jamás afecta la respuesta.
        await _recordar_entidades(memoria_svc, update.chat_id, respuesta)

    return handler


# Mapeo tool→entidad del writer de memoria (ADR 0024): conservador, solo tools con `data` inequívoca.
# `consultar_producto` fija el último producto; `crear_cliente` fija el último cliente.
_ENTIDAD_POR_TOOL: dict[str, str] = {
    "consultar_producto": TIPO_ULTIMO_PRODUCTO,
    "crear_cliente": TIPO_ULTIMO_CLIENTE,
}


async def _recordar_entidades(
    memoria_svc: MemoriaService, chat_id: int, respuesta: RespuestaAgente
) -> None:
    """Persiste la entidad recordada del turno (id + nombre) si la tool y su `data` la identifican."""
    tipo = _ENTIDAD_POR_TOOL.get(respuesta.tool or "")
    data = respuesta.data or {}
    if tipo is None or not data.get("id") or not data.get("nombre"):
        return
    try:
        await memoria_svc.recordar_entidad(chat_id, tipo, {"id": data["id"], "nombre": data["nombre"]})
    except Exception:
        log.warning("memoria_recordar_entidad_fallo", chat_id=chat_id, tipo=tipo, exc_info=True)


def _con_metodo_pago(tool_call: ToolCall, metodo: str) -> ToolCall:
    """Copia el `ToolCall` fijando `metodo_pago` (el ToolCall es inmutable; no se muta el pendiente)."""
    return replace(tool_call, arguments={**tool_call.arguments, "metodo_pago": metodo})


def crear_callback_handler(
    *,
    dispatcher: Dispatcher,
    pendientes: VentaPendienteStore,
    crear_recursos: RecursosFactory,
    memoria: MemoriaFactory | None = None,
    confirm: ConfirmStore | None = None,
) -> CallbackHandler:
    """Captura el dispatcher + el store de pendientes y devuelve el `CallbackHandler` del webhook.

    Según `callback.data`:
      - `pago:<metodo>` → carga el pendiente (si no hay → avisa que expiró), fija `metodo_pago` en el
        `ToolCall`, ejecuta vía `dispatcher.ejecutar` (MISMA `idempotency_key` del pendiente → un
        doble-tap no duplica), responde la confirmación y limpia el pendiente;
      - `venta:cancelar` → limpia el pendiente y responde `MENSAJE_VENTA_CANCELADA`.
    SIEMPRE cierra con `notificador.answer_callback` (Telegram exige el ack o el botón queda "cargando").
    """

    async def handler(
        callback: CallbackBot, ctx: Contexto, session: AsyncSession, notificador: Notificador
    ) -> None:
        data = callback.data or ""
        try:
            if data == CALLBACK_CANCELAR:
                await pendientes.borrar(ctx.tenant_id, callback.chat_id)
                await notificador.responder(callback.chat_id, MENSAJE_VENTA_CANCELADA)
            elif data.startswith(PREFIJO_PAGO):
                await _registrar_con_metodo(
                    data[len(PREFIJO_PAGO):], callback, ctx, session, notificador,
                    dispatcher=dispatcher, pendientes=pendientes,
                    crear_recursos=crear_recursos, memoria=memoria, confirm=confirm,
                )
            else:
                log.info("bot_callback_desconocido", data=data, chat_id=callback.chat_id)
        finally:
            try:
                await notificador.answer_callback(callback.callback_id)
            except Exception:
                log.warning("bot_answer_callback_fallo", chat_id=callback.chat_id, exc_info=True)

    return handler


async def _registrar_con_metodo(
    metodo: str,
    callback: CallbackBot,
    ctx: Contexto,
    session: AsyncSession,
    notificador: Notificador,
    *,
    dispatcher: Dispatcher,
    pendientes: VentaPendienteStore,
    crear_recursos: RecursosFactory,
    memoria: MemoriaFactory | None,
    confirm: ConfirmStore | None = None,
) -> None:
    """Ejecuta la venta pendiente con el `metodo` elegido. Consume el pendiente solo si se registró.

    El pendiente se borra DESPUÉS de ejecutar; un segundo tap ya no lo encuentra (no re-ejecuta), y si
    dos taps corrieran a la par, la `idempotency_key` estable hace que el despachador dedupe el 2º.

    FAIL-CLOSED ante límites por empresa: si el despachador devuelve `Confirmar` (la venta superó un
    umbral con `limite_modo=confirmar`), NO se ejecutó nada. Se re-encamina la confirmación al
    `ConfirmStore` —igual que el camino del modelo— para que el "sí" del usuario la complete
    (`confirmado=True`, MISMA key); sin `ConfirmStore`, la venta simplemente NO se registra (bloqueada).
    El modo `escalar` devuelve `ErrorTool(limite_excedido)`: tampoco ejecuta y se informa.
    """
    pendiente = await pendientes.obtener(ctx.tenant_id, callback.chat_id)
    if pendiente is None:
        await notificador.responder(callback.chat_id, MENSAJE_VENTA_EXPIRADA)
        return
    tool_call = _con_metodo_pago(pendiente.tool_call, metodo)
    ctx2 = replace(ctx, idempotency_key=pendiente.idempotency_key)
    try:
        resultado = await dispatcher.ejecutar(tool_call, ctx2, crear_recursos(session))
    except Exception:
        log.warning("callback_pago_fallo", chat_id=callback.chat_id, exc_info=True)
        await notificador.responder(callback.chat_id, MENSAJE_RESPALDO)
        return
    if isinstance(resultado, Confirmar):
        # La venta NO se registró (el riel/límite cortó antes del handler). Pasar el pendiente —ya con
        # método y key— al ConfirmStore para que el "sí" lo re-despache; sin store, queda bloqueada.
        await pendientes.borrar(ctx.tenant_id, callback.chat_id)
        if confirm is not None:
            try:
                await confirm.guardar(
                    ctx.tenant_id, callback.chat_id,
                    tool_call=tool_call, idempotency_key=pendiente.idempotency_key,
                )
            except Exception:
                log.warning("confirmacion_guardar_fallo", chat_id=callback.chat_id, exc_info=True)
        await notificador.responder(callback.chat_id, texto_de_respuesta(resultado))
        return
    respuesta = texto_de_respuesta(resultado)
    await notificador.responder(callback.chat_id, respuesta)
    await pendientes.borrar(ctx.tenant_id, callback.chat_id)
    if memoria is not None:
        try:
            await memoria(session).guardar_turno(
                callback.chat_id, usuario=f"{PREFIJO_PAGO}{metodo}", asistente=respuesta
            )
        except Exception:
            log.warning("turno_persistencia_fallo", chat_id=callback.chat_id, exc_info=True)
