"""Router del pack de conversación / handoff (backend del dashboard). Gateado por `canal_whatsapp`.

La bandeja de handoff es del canal de cara al cliente: sin el flag `canal_whatsapp`, las rutas
responden 404 (como si no existieran). RBAC: ver las escaladas y resolverlas (devolver al bot) es
OPERATIVO → staff (vendedor+), igual que gestionar citas/bloqueos. La lógica vive en
`ConversacionService`; aquí solo se valida, se mapea a HTTP y se serializa — sin SQL.

Tiempo real: escalar (desde el agente) y resolver (desde aquí) emiten su evento SSE en el repositorio
(`publish` → pg_notify, acotado al tenant), así la bandeja se actualiza en vivo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.wa.agent import MemoriaWa
from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import get_tenant_db
from core.logging import tenant_id_var
from modules.conversaciones.errors import ConversacionInexistente
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.schemas import ConversacionLeer
from modules.conversaciones.service import ConversacionService

# Todo el router exige el flag canal_whatsapp (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/conversaciones", tags=["conversaciones"],
    dependencies=[Depends(require_feature("canal_whatsapp"))],
)


def get_conversacion_service(
    session: AsyncSession = Depends(get_tenant_db),
) -> ConversacionService:
    """Arma el `ConversacionService` sobre la sesión del tenant (los tests lo overridean).

    Le inyecta la memoria del canal (Redis) para que `resolver` la LIMPIE: así el bot retoma en limpio
    y no re-escala por el historial viejo. Cliente Redis perezoso (no conecta hasta usarlo).
    """
    return ConversacionService(
        SqlConversacionRepository(session), memoria=MemoriaWa(url=get_settings().redis_url)
    )


@router.get("/escaladas", response_model=list[ConversacionLeer])
async def listar_escaladas(
    service: ConversacionService = Depends(get_conversacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ConversacionLeer]:
    """Conversaciones en manos de un humano (estado=humano): la bandeja de handoff."""
    return await service.listar_escaladas()


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
