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
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ai.agent import RespuestaAgente, ejecutar_turno
from ai.dispatcher import Dispatcher, Recursos
from ai.envelope import Contexto
from apps.bot.ports import ArchivosTelegram, Notificador, TurnoHandler, UpdateBot
from core.config.timezone import today_co
from core.llm.factory import LLMResuelto, Turno
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

# Factories atadas a la sesión del tenant que el handler recibe en cada turno.
MemoriaFactory = Callable[[AsyncSession], MemoriaService]
CostosFactory = Callable[[AsyncSession], CostosStore]
RecursosFactory = Callable[[AsyncSession], Recursos]
AudioFactory = Callable[[AsyncSession], AudioLogsRepo]
# El bucle del agente, inyectable para pruebas (default: el real).
EjecutarTurno = Callable[..., Awaitable[RespuestaAgente]]


def construir_system_prompt(entidades: dict[str, dict], *, hoy: date | None = None) -> str:
    """System prompt mínimo (helper puro): persona de asistente de ventas + fecha de HOY en
    zona Colombia + bloque "Contexto reciente" con las entidades recordadas (si existen).

    PROHIBIDO meter el inventario o los valores monetarios del negocio: el modelo los obtiene
    por herramientas (tool-calling), no por el prompt.
    """
    hoy = hoy or today_co()
    lineas = [
        "Eres el asistente de ventas de una ferretería. Atiendes en español, de forma breve y "
        "concreta, y ayudas a registrar ventas, gastos, fiados y consultas del negocio.",
        f"Fecha de hoy (Colombia): {hoy.isoformat()}.",
        "Para cualquier dato del negocio usa las herramientas disponibles; nunca inventes valores.",
    ]
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


def crear_turno_handler(
    *,
    dispatcher: Dispatcher,
    memoria: MemoriaFactory,
    costos: CostosFactory,
    crear_recursos: RecursosFactory,
    ejecutar: EjecutarTurno = ejecutar_turno,
    turno: Turno = Turno.WORKER,
    transcriptor: Transcriptor | None = None,
    archivos: ArchivosTelegram | None = None,
    audios: AudioFactory | None = None,
) -> TurnoHandler:
    """Captura el dispatcher + stores (+ voz, opcional) y devuelve el `TurnoHandler` del webhook.

    Las deps de voz (`transcriptor`, `archivos`, `audios`) son OPCIONALES: un despliegue sin voz
    deja el pipeline de texto intacto. Con voz, un update con `voz_file_id` se transcribe ANTES del
    pipeline y el texto resultante entra como si el usuario lo hubiera escrito (E5).
    """

    async def handler(
        update: UpdateBot, ctx: Contexto, session: AsyncSession, notificador: Notificador
    ) -> None:
        # Voz: se resuelve a texto ANTES del pipeline; None = ya se respondió (capacidad/fallo/silencio).
        if update.voz_file_id:
            texto = await _resolver_texto_voz(
                update, ctx, notificador,
                transcriptor=transcriptor, archivos=archivos, audios=audios, session=session,
            )
            if texto is None:
                return
        else:
            texto = update.texto

        memoria_svc = memoria(session)
        historial = await memoria_svc.cargar_historial(update.chat_id)        # best-effort
        entidades = await memoria_svc.leer_entidades(update.chat_id)          # best-effort
        system = construir_system_prompt(entidades)
        recursos = crear_recursos(session)                                    # fresco por turno
        try:
            base = await dispatcher.seleccionar_proveedor(ctx.tenant_id, turno=turno)
            proveedor = LLMResuelto(
                provider=ProveedorMedido(base.provider, costos(session)),     # token accounting
                model=base.model,
                provider_nombre=base.provider_nombre,
            )
            respuesta = await ejecutar(
                texto=texto, ctx=ctx, ejecutor=dispatcher, recursos=recursos,
                proveedor=proveedor, historial=historial, system=system,
            )
        except Exception:
            log.warning("turno_fallo_proveedor", chat_id=update.chat_id, exc_info=True)
            await notificador.responder(update.chat_id, MENSAJE_RESPALDO)
            return

        await notificador.responder(update.chat_id, respuesta.texto)
        # Persistencia best-effort: jamás degrada la respuesta ya enviada (try propio, sin respaldo).
        try:
            await memoria_svc.guardar_turno(
                update.chat_id, usuario=texto or "", asistente=respuesta.texto
            )
        except Exception:
            log.warning("turno_persistencia_fallo", chat_id=update.chat_id, exc_info=True)

    return handler
