"""Orquestador de la ingesta Bancolombia por tenant: historyId → mensajes → filtro → persistir →
Telegram → SSE. Lo invoca el job del worker (`procesar_gmail_push`); aquí no hay wiring ni red directa
(el cliente Gmail y los servicios se inyectan), así que es testeable con fakes.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from core.config.timezone import COLOMBIA_TZ, now_co, today_co
from core.logging import get_logger
from modules.bancos.gmail import parser
from modules.bancos.gmail.cliente import GmailCliente
from modules.bancos.repository import SqlBancosRepository

log = get_logger("bancos.gmail.ingesta")


@dataclass(slots=True)
class ResultadoIngesta:
    procesados: int = 0            # mensajes leídos que eran transferencias entrantes
    insertados: int = 0           # filas nuevas (idempotencia: repetidos no cuentan)
    notificados: int = 0
    nuevo_history_id: str | None = None
    ids_mensajes: list[str] = field(default_factory=list)


def _fecha_de(fecha_str: str) -> date:
    """'DD/MM/YYYY' → date; hoy Colombia si no parsea (no perder la fila por un formato raro)."""
    try:
        return datetime.strptime(fecha_str, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return today_co()


async def procesar_push(
    *,
    cliente: GmailCliente,
    repo: SqlBancosRepository,
    last_history_id: str | None,
    notificar: Callable[[str], Awaitable[None]],
    publicar: Callable[[dict], Awaitable[None]] | None = None,
    history_id_push: str | None = None,
) -> ResultadoIngesta:
    """Procesa los mensajes nuevos desde `last_history_id`. Idempotente por `gmail_message_id`.

    Sin `last_history_id` (primer push tras activar el watch) no hay rango que releer: se adopta el
    `history_id_push` como punto de partida y se sale (los siguientes push ya traen delta). El envío a
    Telegram y el SSE ocurren SOLO para filas realmente insertadas (repetidos no re-notifican).
    """
    resultado = ResultadoIngesta(nuevo_history_id=last_history_id)
    if not last_history_id:
        resultado.nuevo_history_id = history_id_push
        return resultado

    ids = await cliente.ids_desde_history(last_history_id)
    for mid in ids:
        headers = await cliente.headers(mid)
        if not parser.es_transferencia_entrante(headers):
            continue
        completo = await cliente.mensaje_completo(mid)
        if completo is None:
            continue
        body = parser.extraer_body(completo.get("payload", {}))
        if not parser.es_dinero_entrante(body):
            continue
        datos = parser.parsear_email_bancolombia(body)
        resultado.procesados += 1

        mov = await repo.ingestar_gmail(
            gmail_message_id=mid,
            fecha=_fecha_de(datos["fecha_str"]),
            monto=Decimal(datos["monto"]),
            remitente=datos["remitente"] or None,
            descripcion=datos["descripcion"] or None,
            tipo_transaccion=datos["tipo"] or None,
            hora=datos["hora"] or None,
        )
        if mov is None:            # ya ingerido antes (dedup por gmail_message_id) → no re-notificar
            continue
        resultado.insertados += 1
        resultado.ids_mensajes.append(mid)

        _, subject = parser.leer_headers(headers)
        mensaje = parser.construir_mensaje(datos, subject, now_co().strftime("%H:%M"))
        try:
            await notificar(mensaje)
            resultado.notificados += 1
        except Exception:
            log.warning("gmail_ingesta_telegram_fallo", gmail_message_id=mid, exc_info=True)
        if publicar is not None:
            try:
                await publicar({
                    "id": mov.id, "monto": str(mov.monto), "remitente": mov.remitente,
                    "tipo": mov.tipo_transaccion, "fecha": mov.fecha.isoformat(),
                })
            except Exception:
                log.warning("gmail_ingesta_sse_fallo", gmail_message_id=mid, exc_info=True)

    # Avanzar el puntero al history del push (o dejar el previo si el push no lo trajo).
    resultado.nuevo_history_id = history_id_push or last_history_id
    log.info("gmail_ingesta", procesados=resultado.procesados, insertados=resultado.insertados)
    return resultado
