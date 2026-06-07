"""Adaptador del vendor Kapso (BSP): el ÚNICO lugar que conoce la forma del webhook y la API de Kapso.

Contrato (https://docs.kapso.ai):
  - Entrada: webhook con headers `X-Webhook-Event`, `X-Webhook-Signature` (HMAC-SHA256 hex del cuerpo
    crudo con el secreto), `X-Idempotency-Key`, `X-Webhook-Payload-Version`. NO hay handshake GET:
    la verificación es por firma. Evento de mensaje entrante: `whatsapp.message.received`.
  - Salida: `POST {base}/{phone_number_id}/messages` con header `X-API-Key` y cuerpo estilo Cloud API.

La firma se valida sobre el **cuerpo crudo** (los bytes tal cual llegaron): re-serializar el JSON
cambiaría el orden/espaciado y rompería el HMAC.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

# Evento de Kapso que traemos: mensaje entrante de un cliente.
EVENTO_MENSAJE = "whatsapp.message.received"


@dataclass(frozen=True, slots=True)
class MensajeWa:
    """Mensaje entrante parseado a lo mínimo que el canal necesita (sin atarse al payload de Kapso)."""

    message_id: str          # message.id (wamid) — identidad del mensaje, base del dedup
    telefono: str            # message.from — el cliente que escribe (su número de WhatsApp)
    phone_number_id: str     # phone_number_id — el número/canal que recibió → resuelve el tenant
    texto: str               # message.text.body


def verificar_firma(secret: str | None, cuerpo: bytes, firma: str | None) -> bool:
    """Valida la firma HMAC-SHA256 de Kapso en tiempo constante. Fail-closed.

    Sin secreto configurado o sin header de firma → NO valida (no se procesa nada sin firma válida).
    """
    if not secret or not firma:
        return False
    esperado = hmac.new(secret.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma)


def parsear_mensaje(payload: dict[str, Any]) -> MensajeWa | None:
    """Extrae el mensaje del payload `whatsapp.message.received`. None si no es texto procesable.

    Solo texto por ahora (otros tipos se ignoran). El `phone_number_id` viene en el nivel superior
    (con respaldo en `conversation.phone_number_id`).
    """
    mensaje = payload.get("message")
    if not isinstance(mensaje, dict) or mensaje.get("type") != "text":
        return None
    message_id = mensaje.get("id")
    telefono = mensaje.get("from")
    texto = (mensaje.get("text") or {}).get("body")
    conversacion = payload.get("conversation") or {}
    phone_number_id = payload.get("phone_number_id") or conversacion.get("phone_number_id")
    if not (message_id and telefono and texto and phone_number_id):
        return None
    return MensajeWa(
        message_id=str(message_id),
        telefono=str(telefono),
        phone_number_id=str(phone_number_id),
        texto=str(texto),
    )


class KapsoSender:
    """Envío saliente vía la API de Kapso (`POST {base}/{phone_number_id}/messages`, header X-API-Key).

    La API key es de plataforma (env, nunca hardcode). El cliente httpx se inyecta en tests; en
    producción se crea perezoso por envío (import dentro del método: nada de red al cargar el módulo).
    """

    def __init__(
        self, api_key: str, *, base_url: str, client: Any | None = None, timeout: float = 10.0
    ) -> None:
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    async def enviar_texto(self, *, phone_number_id: str, to: str, texto: str) -> dict[str, Any]:
        """Envía un mensaje de texto. Devuelve el JSON de Kapso (incluye `messages[0].id`)."""
        url = f"{self._base}/{phone_number_id}/messages"
        cuerpo = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": texto, "preview_url": False},
        }
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}
        if self._client is not None:
            resp = await self._client.post(url, json=cuerpo, headers=headers)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout) as cliente:
                resp = await cliente.post(url, json=cuerpo, headers=headers)
        resp.raise_for_status()
        return resp.json()
