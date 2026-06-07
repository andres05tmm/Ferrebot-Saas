"""Puertos y tipos del adaptador de canal WhatsApp (webhook único de Kapso).

El orquestador del webhook (`apps.wa.webhook.manejar_mensaje`) depende de estos `Protocol`s, no de
implementaciones concretas — igual que el webhook del bot. Así se prueba con fakes (sin red, sin
Redis, sin control DB) la regla de seguridad: validar la firma ANTES de procesar, dedup por id de
mensaje, y resolver el tenant por `phone_number_id`. Las implementaciones reales viven en
`apps.wa.wiring`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ai.envelope import Contexto
from apps.wa.kapso import MensajeWa
from core.tenancy.context import ResolvedTenant


class WaTenantResolver(Protocol):
    """Resuelve la empresa por el número/canal de Kapso (`phone_number_id`)."""

    async def por_phone_number_id(self, phone_number_id: str) -> ResolvedTenant | None: ...


class WaDedup(Protocol):
    """Dedup por id de mensaje de Kapso (un reintento del webhook no se procesa dos veces)."""

    async def marcar_si_nuevo(self, message_id: str) -> bool: ...


# Procesa un mensaje ya validado: recibe el mensaje y el Contexto del pack (tenant + cliente_telefono).
# En este entregable es el ECO (encola el envío); en el siguiente será el bucle del agente.
ProcesadorWa = Callable[[MensajeWa, Contexto], Awaitable[None]]


class AccionWa(str, Enum):
    """Desenlace de un webhook entrante; el adaptador HTTP lo mapea a un status."""

    PROCESADO = "procesado"
    DUPLICADO = "duplicado"                   # message_id repetido (reintento del webhook)
    NO_MAPEADO = "no_mapeado"                 # phone_number_id sin empresa en wa_numeros
    EMPRESA_INACTIVA = "empresa_inactiva"     # mapea a una empresa no activa
    FIRMA_INVALIDA = "firma_invalida"         # X-Webhook-Signature ausente o que no coincide
    EVENTO_IGNORADO = "evento_ignorado"       # evento que no es whatsapp.message.received
    MENSAJE_IGNORADO = "mensaje_ignorado"     # sin texto procesable (otro tipo de mensaje)
    BODY_INVALIDO = "body_invalido"           # cuerpo JSON inválido


@dataclass(frozen=True, slots=True)
class ResultadoWa:
    """Resultado de un webhook: la acción, el status HTTP y, si procesó, el Contexto construido."""

    accion: AccionWa
    status: int
    ctx: Contexto | None = None


@dataclass(frozen=True, slots=True)
class WaDeps:
    """Dependencias del webhook (inyectadas por el composition root)."""

    webhook_secret: str | None          # secreto de plataforma para validar la firma de Kapso
    resolver: WaTenantResolver
    dedup: WaDedup
    procesar: ProcesadorWa
