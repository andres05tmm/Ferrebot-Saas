"""Envío de email transaccional vía Brevo (API v3, `smtp/email`). Puerto + fallback + fábrica.

Solo se usa hoy para el enlace de reset de contraseña (ADR 0009 §D3), pero el puerto es genérico. La
credencial (`BREVO_API_KEY`) es de PLATAFORMA (una cuenta atiende a todos los tenants): va en el
entorno, JAMÁS en código (regla #5). Sin API key → `LogSender` (dev, o antes de configurar Brevo):
el flujo no se rompe, solo no se envía. El remitente debe estar en un dominio AUTENTICADO en Brevo
(SPF/DKIM) o el correo cae en spam.

Diseño alineado con el resto del repo:
- Puerto `EmailSender` (Protocol) inyectable por dependencia → el endpoint se prueba con un fake, sin red.
- Cliente httpx opcional inyectable (mismo patrón que `modules/bancos/gmail/cliente`).
- Best-effort: un fallo de envío se loguea y se traga (no tumba el request ni filtra si el email existe
  → preserva la NO enumeración del flujo de reset). El enlace/token NUNCA se loguea (es un secreto).
"""
from __future__ import annotations

from typing import Protocol

import httpx

from core.logging import get_logger

log = get_logger("email")

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_ASUNTO_RESET = "Restablece tu contraseña"


class EmailSender(Protocol):
    """Envía el correo con el enlace de reset a `email`. Best-effort: no lanza."""

    async def enviar_reset(self, email: str, enlace: str) -> None: ...


class LogSender:
    """Fallback sin proveedor configurado: registra que NO se envió, sin el email ni el enlace en claro."""

    async def enviar_reset(self, email: str, enlace: str) -> None:
        log.warning("email_no_configurado_reset_no_enviado")


def _cuerpo_texto(enlace: str) -> str:
    return (
        "Recibimos una solicitud para restablecer tu contraseña.\n\n"
        f"Abre este enlace para elegir una nueva (vence en 1 hora):\n{enlace}\n\n"
        "Si no fuiste tú, ignora este correo: tu contraseña actual sigue vigente."
    )


def _cuerpo_html(enlace: str) -> str:
    return (
        '<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:15px;color:#1a1a1a">'
        "<p>Recibimos una solicitud para restablecer tu contraseña.</p>"
        f'<p><a href="{enlace}" style="display:inline-block;padding:10px 18px;background:#111;'
        'color:#fff;border-radius:8px;text-decoration:none">Elegir nueva contraseña</a></p>'
        "<p style=\"color:#666;font-size:13px\">El enlace vence en 1 hora. "
        "Si no fuiste tú, ignora este correo: tu contraseña actual sigue vigente.</p>"
        "</div>"
    )


def construir_payload(remitente: str, remitente_nombre: str, email: str, enlace: str) -> dict:
    """Payload de la API v3 de Brevo (`POST /v3/smtp/email`) para el correo de reset. Compartido por el
    sender y el verificador (`tools/test_email`) para que la prueba use EXACTAMENTE el cuerpo de producción."""
    return {
        "sender": {"email": remitente, "name": remitente_nombre},
        "to": [{"email": email}],
        "subject": _ASUNTO_RESET,
        "htmlContent": _cuerpo_html(enlace),
        "textContent": _cuerpo_texto(enlace),
    }


class BrevoSender:
    """Envía por la API v3 de Brevo. El remitente debe estar en un dominio AUTENTICADO (SPF/DKIM)."""

    def __init__(
        self,
        api_key: str,
        remitente: str,
        remitente_nombre: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = api_key
        self._from = remitente
        self._from_nombre = remitente_nombre
        self._http = http

    def _payload(self, email: str, enlace: str) -> dict:
        return construir_payload(self._from, self._from_nombre, email, enlace)

    async def enviar_reset(self, email: str, enlace: str) -> None:
        # Brevo autentica con el header `api-key` (NO Bearer). Éxito = 201 Created.
        headers = {"api-key": self._key, "Content-Type": "application/json", "accept": "application/json"}
        payload = self._payload(email, enlace)
        try:
            if self._http is not None:
                resp = await self._http.post(_BREVO_URL, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=15) as c:
                    resp = await c.post(_BREVO_URL, json=payload, headers=headers)
            resp.raise_for_status()
            log.info("email_reset_enviado")   # auditoría: salió el correo (sin PII ni enlace)
        except Exception:
            # Best-effort: nunca se filtra al usuario ni se tumba el request. Sin el enlace en el log.
            log.error("email_reset_envio_fallo", exc_info=True)


def construir_sender(
    api_key: str, remitente: str, remitente_nombre: str
) -> EmailSender:
    """Brevo si hay API key; si no, el fallback que solo loguea (dev / Brevo sin configurar)."""
    if not api_key:
        return LogSender()
    return BrevoSender(api_key, remitente, remitente_nombre)
