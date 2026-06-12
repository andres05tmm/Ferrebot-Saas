"""Router del pack de conversación / handoff — el INBOX con hand-off bidireccional. Gateado por
`canal_whatsapp`.

El inbox es del canal de cara al cliente: sin el flag `canal_whatsapp`, las rutas responden 404 (como
si no existieran). RBAC: ver el inbox, tomar/responder/devolver al bot es OPERATIVO → staff
(vendedor+), igual que gestionar citas. La lógica vive en `ConversacionService`; aquí solo se valida,
se mapea a HTTP y se serializa — sin SQL.

Rutas (modelo Chatwoot sobre el estado `bot`/`humano`):
  - GET    /conversaciones                  → inbox: todas, con último mensaje y estado.
  - GET    /conversaciones/escaladas         → solo las que esperan humano (compat / home de agente).
  - GET    /conversaciones/{id}/mensajes     → hilo ordenado.
  - POST   /conversaciones/{id}/responder    → el asesor responde (estado=humano); manda por Kapso.
  - POST   /conversaciones/{id}/tomar        → takeover: estado→humano (pausa el bot).
  - POST   /conversaciones/{id}/resolver     → devuelve al bot (estado→bot) y limpia la memoria.

Tiempo real: cada transición y cada mensaje emiten su evento SSE en el repositorio (`publish` →
pg_notify, acotado al tenant), así el inbox se actualiza en vivo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.wa.agent import MemoriaWa
from apps.wa.kapso import KapsoSender
from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from core.logging import tenant_id_var
from core.tenancy.control_repo import phone_number_id_activo
from modules.conversaciones.errors import (
    ConversacionInexistente,
    ConversacionNoEnHumano,
    SinCanalWhatsapp,
)
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.schemas import (
    ConversacionInbox,
    ConversacionLeer,
    MensajeLeer,
    ResponderEntrada,
)
from modules.conversaciones.service import ConversacionService, EnviadorWa

# Todo el router exige el flag canal_whatsapp (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/conversaciones", tags=["conversaciones"],
    dependencies=[Depends(require_feature("canal_whatsapp"))],
)


class KapsoEnviadorWa:
    """Adaptador de envío saliente: resuelve el número activo del tenant (control DB) y manda por Kapso.

    El `phone_number_id` se resuelve por tenant en una sesión de control FRESCA por envío; la API key es
    de plataforma (env). Si la empresa no tiene número activo → `SinCanalWhatsapp` (el router → 409).
    """

    def __init__(self, sender: KapsoSender) -> None:
        self._sender = sender

    async def enviar(self, tenant_id: int, to: str, texto: str) -> None:
        async with control_session() as cs:
            phone_number_id = await phone_number_id_activo(cs, tenant_id)
        if phone_number_id is None:
            raise SinCanalWhatsapp()
        await self._sender.enviar_texto(phone_number_id=phone_number_id, to=to, texto=texto)


def get_enviador_wa() -> EnviadorWa:
    """Adaptador de envío saliente Kapso (los tests lo overridean con un doble que no toca red)."""
    s = get_settings()
    return KapsoEnviadorWa(KapsoSender(s.kapso_api_key, base_url=s.kapso_api_base))


def get_conversacion_service(
    session: AsyncSession = Depends(get_tenant_db),
    enviador: EnviadorWa = Depends(get_enviador_wa),
) -> ConversacionService:
    """Arma el `ConversacionService` sobre la sesión del tenant (los tests lo overridean).

    Le inyecta la memoria del canal (Redis) para que `resolver` la LIMPIE (el bot retoma en limpio) y
    el `enviador` de Kapso para que el asesor responda. Clientes perezosos (no conectan hasta usarlos).
    """
    return ConversacionService(
        SqlConversacionRepository(session),
        memoria=MemoriaWa(url=get_settings().redis_url),
        enviador=enviador,
    )


@router.get("", response_model=list[ConversacionInbox])
async def listar_inbox(
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ConversacionInbox]:
    """Inbox completo: todas las conversaciones con su último mensaje y estado (lista izquierda)."""
    filas = await service.listar_inbox()
    return [
        ConversacionInbox(
            **ConversacionLeer.model_validate(f.conversacion).model_dump(),
            ultimo_texto=f.ultimo.texto if f.ultimo else None,
            ultimo_autor=f.ultimo.autor if f.ultimo else None,
            ultimo_en=f.ultimo.creada_en if f.ultimo else None,
        )
        for f in filas
    ]


@router.get("/escaladas", response_model=list[ConversacionLeer])
async def listar_escaladas(
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ConversacionLeer]:
    """Conversaciones en manos de un humano (estado=humano): la bandeja de handoff."""
    return await service.listar_escaladas()


@router.get("/{conversacion_id}/mensajes", response_model=list[MensajeLeer])
async def listar_mensajes(
    conversacion_id: int,
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[MensajeLeer]:
    """Hilo de la conversación (entrante/saliente · cliente/bot/asesor), en orden cronológico."""
    try:
        return await service.listar_mensajes(conversacion_id)
    except ConversacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/{conversacion_id}/responder", response_model=MensajeLeer)
async def responder_conversacion(
    conversacion_id: int,
    entrada: ResponderEntrada,
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> MensajeLeer:
    """El asesor responde al cliente (Kapso) y se persiste el saliente (`autor=asesor`).

    Exige `estado=humano` (409 si no). El `tenant_id` (contextvar del `TenantMiddleware`) acota el
    número por el que se envía y la base donde se persiste.
    """
    try:
        return await service.responder(
            conversacion_id, entrada.texto, tenant_id=tenant_id_var.get()
        )
    except ConversacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ConversacionNoEnHumano as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SinCanalWhatsapp as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{conversacion_id}/tomar", response_model=ConversacionLeer)
async def tomar_conversacion(
    conversacion_id: int,
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ConversacionLeer:
    """Takeover manual: pasa la conversación a `humano` (pausa el bot) aunque no la haya escalado él."""
    try:
        return await service.tomar(conversacion_id)
    except ConversacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/{conversacion_id}/resolver", response_model=ConversacionLeer)
async def resolver_conversacion(
    conversacion_id: int,
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ConversacionLeer:
    """Devuelve la conversación al bot (estado→bot, sella resuelta_en) y limpia su memoria de Redis.

    El `tenant_id` (del contextvar que liga el `TenantMiddleware`) acota la clave de memoria del
    cliente; al limpiarla, el agente vuelve a atender sin el historial viejo (no re-escala).
    """
    try:
        return await service.resolver(conversacion_id, tenant_id=tenant_id_var.get())
    except ConversacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
