"""Composition root del canal Telegram público: arma `TgPublicoDeps` (webhook) y `construir_agente_tg`.

Smoke manual (como `apps.wa.wiring` / `apps.worker.main`): la lógica vive en `apps.tg_publico.webhook`
y en el `AgenteWa` (testeables con fakes). Aquí solo el cableado:
  - `construir_tg_deps(arq_pool)`  → lo llama el lifespan del API (espejo de `construir_wa_deps`).
  - `construir_agente_tg(settings)` → lo llama `on_startup` del worker para el seam `ctx["tg_agente"]`.
"""
from __future__ import annotations

from typing import Any

from ai.envelope import Contexto
from apps.tg_publico.ports import TgPublicoDeps, UpdateTgPublico
from apps.tg_publico.repos import SecretosTgPublico
from apps.tg_publico.sender import TelegramPublicoSender
from apps.wa.agent import AgenteWa, MemoriaWa
from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.llm.factory import LLMResuelto, PlataformaLLM, Turno, get_llm_con_fallback
from core.llm.gobierno import Gobierno, PoliticaGobierno, RedisGobierno
from core.llm.stores import ControlLLMConfigStore, ControlLLMKeyStore
from core.logging import get_logger
from core.pagos.bold import BoldClient
from core.pagos.config import cargar_config_bold
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import resolve_tenant_by_slug
from modules.agenda.gcal import calendar_client_por_defecto

log = get_logger("tg_publico.wiring")

_TTL_DEDUP = 86_400  # 24h: cubre de sobra los reintentos del webhook de Telegram

# Nombre del job ARQ del agente (registrado en apps.worker.main): el webhook encola, el worker atiende.
JOB_AGENTE = "atender_mensaje_tg"


class ControlTgResolver:
    """Resuelve el tenant por slug abriendo una sesión de control FRESCA por llamada."""

    async def por_slug(self, slug: str) -> ResolvedTenant | None:
        async with control_session() as cs:
            return await resolve_tenant_by_slug(cs, slug)


class ControlTgSecretos:
    """Lee el secret-token del webhook del canal público (control DB), sesión fresca por llamada."""

    def __init__(self, master_key: str) -> None:
        self._master = master_key

    async def webhook_secret(self, empresa_id: int) -> str | None:
        async with control_session() as cs:
            return await SecretosTgPublico(cs, self._master).webhook_secret(empresa_id)


class RedisTgDedup:
    """Dedup por `(tenant, update_id)`: `SET NX EX tg_publico:dedup:{tenant}:{update_id}`. Cliente Redis
    perezoso e inyectable (mismo patrón que `RedisWaDedup`)."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    def _redis(self) -> Any:
        if self._client is None:
            self._client = _cliente_redis(self._url)
        return self._client

    async def marcar_si_nuevo(self, tenant_id: int, update_id: int) -> bool:
        clave = f"tg_publico:dedup:{tenant_id}:{update_id}"
        marcado = await self._redis().set(clave, "1", nx=True, ex=_TTL_DEDUP)
        return bool(marcado)

    async def desmarcar(self, tenant_id: int, update_id: int) -> None:
        await self._redis().delete(f"tg_publico:dedup:{tenant_id}:{update_id}")


class ProcesadorTgAgente:
    """Encola el turno del agente en ARQ (no corre el LLM en el hilo del webhook). Pasa la identidad
    del cliente DEL CONTEXTO (que vino del payload): tenant_id, chat_id, texto, update_id."""

    def __init__(self, encolar) -> None:
        self._encolar = encolar  # Callable[..., Awaitable] (arq_pool.enqueue_job)

    async def __call__(self, update: UpdateTgPublico, ctx: Contexto) -> None:
        args = [JOB_AGENTE, ctx.tenant_id, update.chat_id, update.texto, update.update_id]
        # Una FOTO agrega el file_id al final (param opcional del job); el texto queda como estaba.
        if update.foto_file_id is not None:
            args.append(update.foto_file_id)
        await self._encolar(*args)


def construir_tg_deps(arq_pool: Any) -> TgPublicoDeps:
    """Arma `TgPublicoDeps` con los adaptadores reales (lo llama el lifespan del API, con su pool ARQ)."""
    s = get_settings()
    return TgPublicoDeps(
        resolver=ControlTgResolver(),
        secretos=ControlTgSecretos(s.secrets_master_key),
        dedup=RedisTgDedup(url=s.redis_url),
        procesar=ProcesadorTgAgente(encolar=arq_pool.enqueue_job),
    )


# ── Seam del worker: el AgenteWa con sender de Telegram ───────────────────────
#
# ponytail: espeja `apps.worker.main._construir_agente` (mismos colaboradores LLM/gobierno/memoria);
# la ÚNICA diferencia es el sender (Telegram en vez de Kapso). Upgrade path si se cansa el copy: sacar
# `_construir_agente` a un helper parametrizado por sender y llamarlo desde ambos canales.


class _ConfigControl:
    """ConfigStore del factory LLM: abre una sesión de control fresca por llamada."""

    async def overrides(self, empresa_id: int) -> dict[str, str]:
        async with control_session() as s:
            return await ControlLLMConfigStore(s).overrides(empresa_id)


class _KeyControl:
    """KeyStore del factory LLM: descifra la key del proveedor en una sesión de control por llamada."""

    def __init__(self, master: str) -> None:
        self._master = master

    async def api_key(self, empresa_id: int, provider: str) -> str | None:
        async with control_session() as s:
            return await ControlLLMKeyStore(s, self._master).api_key(empresa_id, provider)


def construir_agente_tg(settings) -> AgenteWa:
    """Arma el `AgenteWa` del canal público de Telegram: mismo cerebro que WhatsApp, sender de Telegram."""
    plataforma = PlataformaLLM.desde_settings(settings)
    config_store, key_store = _ConfigControl(), _KeyControl(settings.secrets_master_key)

    async def resolver_llm(tenant_id: int, turno: Turno):
        # Con resiliencia (ADR 0023): retry ante transitorios + respaldo si está configurado.
        return await get_llm_con_fallback(
            tenant_id, turno=turno, config_store=config_store, key_store=key_store,
            plataforma=plataforma,
        )

    async def capacidades(tenant_id: int) -> frozenset[str]:
        async with control_session() as s:
            return await ControlCapacidades(s).efectivas(tenant_id)

    async def resolver_psp(tenant_id: int):
        """PSP Bold del tenant (ADR 0013): None si no tiene llave (modo manual)."""
        async with control_session() as s:
            cred = await cargar_config_bold(s, settings.secrets_master_key, tenant_id)
        return BoldClient(cred) if cred is not None else None

    gobierno = Gobierno(
        store=RedisGobierno(url=settings.redis_url),
        plataforma=PoliticaGobierno.desde_settings(settings),
        config_store=config_store,
    )
    return AgenteWa(
        abrir_tenant=tenant_session,
        resolver_llm=resolver_llm,
        capacidades=capacidades,
        memoria=MemoriaWa(url=settings.redis_url),
        sender=TelegramPublicoSender(settings.secrets_master_key),
        gcal=calendar_client_por_defecto(),
        resolver_psp=resolver_psp,
        gobierno=gobierno,
    )


async def resolver_vision_tg(tenant_id: int) -> LLMResuelto:
    """(Proveedor + modelo) con VISIÓN del tenant para `extraer_recibo` del comprobante del cliente.

    Espeja `apps.bot.wiring.resolver_vision` del Bot PIM: turno ORQUESTADOR (el modelo capaz, con
    visión), mismo factory por empresa + resiliencia (retry/respaldo, ADR 0023) que usa el `AgenteWa`.
    Abre sesiones de control frescas por llamada (los stores del factory lo hacen internamente).
    """
    s = get_settings()
    return await get_llm_con_fallback(
        tenant_id,
        turno=Turno.ORQUESTADOR,
        config_store=_ConfigControl(),
        key_store=_KeyControl(s.secrets_master_key),
        plataforma=PlataformaLLM.desde_settings(s),
    )


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
