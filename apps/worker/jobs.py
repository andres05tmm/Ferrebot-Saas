"""Lógica testeable de los jobs del worker (separada del runtime ARQ de `apps.worker.main`).

Aquí vive la lógica de cada job (testeable con dobles/BD efímera); el cableado del runtime (Redis,
reintentos, seams de `on_startup`) está en `apps.worker.main`. Jobs:

- `atender_mensaje_wa` — atiende un mensaje de WhatsApp con el agente de agenda (encolado por el webhook).
- `emitir_documento` — emite una factura y traduce la `Decision` de `service.emitir` a la semántica del
  worker (reintentar con backoff, dead-letter o terminal). El backoff es una función pura.
- `provisionar_tenant` — aprovisiona un tenant desde un manifiesto (panel super-admin, ADR 0010 §B2): la
  pieza pesada/privilegiada (CREATE DATABASE + cifrado), con validación server-side, slug estricto, lock
  por slug, estado observable en Redis y errores sanitizados (secretos jamás al log ni al estado).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from arq import Retry
from pydantic import ValidationError

from apps.wa.kapso import MensajeWa
from core.config import get_settings
from core.config.timezone import now_co
from core.logging import get_logger
from tools.manifest import ErrorManifiesto, validar
from tools.manifest.schema import Manifiesto, slug_valido
from tools.provision_from_manifest import provision_from_manifest_obj

log = get_logger("worker.facturacion")


def _backoff(job_try: int, *, base: int = 30, tope: int = 3600) -> int:
    """Backoff exponencial acotado: `min(base * 2 ** (job_try - 1), tope)` segundos. PURO."""
    return min(base * 2 ** (job_try - 1), tope)


async def atender_mensaje_wa(
    ctx: dict, tenant_id: int, phone_number_id: str, telefono: str, texto: str, message_id: str
) -> str:
    """Atiende un mensaje de WhatsApp con el agente de agenda (encolado por el webhook).

    Seams inyectados por `on_startup`: `ctx["resolver_tenant"]` (tenant_id → ResolvedTenant) y
    `ctx["wa_agente"]` (el `AgenteWa` que corre el bucle LLM + herramientas y responde por Kapso).
    """
    tenant = await ctx["resolver_tenant"](tenant_id)
    if tenant is None:
        log.warning("wa_job_sin_tenant", tenant_id=tenant_id)
        return "sin_tenant"
    mensaje = MensajeWa(
        message_id=message_id, telefono=telefono, phone_number_id=phone_number_id, texto=texto
    )
    await ctx["wa_agente"].atender(mensaje, tenant)
    return "atendido"


# ── Provisioning del panel super-admin (ADR 0010 §B2) ────────────────────────
#
# El job es la pieza PESADA/PRIVILEGIADA (CREATE DATABASE + cifrado), por eso vive en el worker (en-red,
# con ADMIN_DATABASE_URL + SECRETS_MASTER_KEY) y no detrás de un request del API. Guardarraíles no
# negociables: validación server-side, slug estricto, lock por slug, estado observable, secretos jamás
# al log ni en el estado/error.

_log_provision = get_logger("worker.provisioning")


class SlugInvalido(ValueError):
    """El slug no cumple el patrón estricto (vector de inyección: se vuelve nombre de base)."""


def _cliente_redis(url: str) -> Any:
    """Cliente Redis async (perezoso): importa `redis.asyncio` al invocar (patrón de modules.auth)."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


def _sanitizar_error(exc: Exception) -> str:
    """Mensaje SEGURO para el estado/log del job: una categoría, NUNCA el texto crudo del error.

    El str() de una excepción puede arrastrar secretos del manifiesto, una URL de conexión con clave o
    una ruta interna (p. ej. un error de psycopg). Se mapea a una categoría estable y opaca.
    """
    if isinstance(exc, SlugInvalido):
        return "slug inválido"
    if isinstance(exc, (ErrorManifiesto, ValidationError)):
        return "manifiesto inválido"
    return "fallo de provisioning"


def _slug_inseguro(manifiesto_dict: dict) -> str | None:
    """Slug declarado (best-effort, truncado) solo para diagnosticar el estado de error. No es secreto."""
    identidad = manifiesto_dict.get("identidad") if isinstance(manifiesto_dict, dict) else None
    slug = identidad.get("slug") if isinstance(identidad, dict) else None
    return slug[:60] if isinstance(slug, str) else None


class EstadoProvision:
    """Estado del job de provisioning en Redis: `job_id → {estado, slug, resumen, error, ...}` con TTL.

    Estados: encolado (lo fija el enrolador del panel, B3) | corriendo | ok | error. NUNCA eco de secretos:
    el `error` se guarda ya sanitizado (`_sanitizar_error`) y el resumen es la línea del provisionador
    (conteos por tabla, sin secretos).
    """

    _PREFIJO = "provision:estado:"

    def __init__(self, redis: Any, ttl_segundos: int) -> None:
        self._r = redis
        self._ttl = ttl_segundos

    @classmethod
    def _key(cls, job_id: str) -> str:
        return f"{cls._PREFIJO}{job_id}"

    async def obtener(self, job_id: str) -> dict | None:
        raw = await self._r.get(self._key(job_id))
        return json.loads(raw) if raw else None

    async def marcar(self, job_id: str, estado: str, **campos: Any) -> dict:
        """UPSERT del estado (conserva `creado_en`, refresca `actualizado_en`). Renueva el TTL."""
        actual = await self.obtener(job_id) or {"job_id": job_id, "creado_en": now_co().isoformat()}
        actual.update(estado=estado, actualizado_en=now_co().isoformat(), **campos)
        await self._r.set(self._key(job_id), json.dumps(actual), ex=self._ttl)
        return actual


async def provisionar_tenant(ctx: dict, manifiesto_dict: dict, job_id: str) -> str:
    """Aprovisiona un tenant desde un manifiesto (dict encolado). Job ARQ del panel (ADR 0010 §B2).

    Coreografía: re-parsea+re-valida SERVER-SIDE (nunca confía en lo encolado) → publica estado → toma un
    LOCK por slug en Redis (serializa la carrera de CREATE DATABASE) → corre la coreografía idempotente
    `provision_from_manifest_obj` en un hilo (es sync) → publica ok/error en cada transición. El error va
    SANITIZADO (sin secretos ni rutas); el manifiesto/secretos jamás se loguean.
    """
    settings = get_settings()
    redis = _cliente_redis(settings.redis_url)
    estado = EstadoProvision(redis, settings.provision_estado_ttl_segundos)
    try:
        # 1) Validación server-side ANTES de tocar la BD. El slug se RE-valida explícitamente PRIMERO
        #    (es el vector de inyección: se vuelve nombre de base); luego forma/tipos (esquema) y
        #    semántica (`validar`). El esquema también lleva el patrón: doble red.
        try:
            slug_declarado = _slug_inseguro(manifiesto_dict)
            if not slug_valido(slug_declarado):
                raise SlugInvalido(str(slug_declarado))
            manifiesto = Manifiesto.model_validate(manifiesto_dict)
            validar(manifiesto)
        except Exception as exc:   # noqa: BLE001 — inválido → estado=error SIN tocar ninguna base
            slug = _slug_inseguro(manifiesto_dict)
            await estado.marcar(job_id, "error", slug=slug, error=_sanitizar_error(exc))
            _log_provision.warning("provision_job_invalido", job_id=job_id, slug=slug, tipo=type(exc).__name__)
            return "error"

        slug = manifiesto.identidad.slug
        await estado.marcar(job_id, "corriendo", slug=slug)

        # 2) LOCK por slug: dos jobs del mismo slug no corren CREATE DATABASE a la vez. `timeout` expira el
        #    lock si el worker muere (anti-deadlock); `blocking_timeout` espera a que el otro libere.
        lock = redis.lock(f"provision:lock:{slug}", timeout=600, blocking_timeout=300)
        try:
            async with lock:
                resumenes: list[str] = []
                # Coreografía SYNC (psycopg + CREATE DATABASE): a un hilo para no bloquear el event loop.
                empresa_id = await asyncio.to_thread(
                    provision_from_manifest_obj, manifiesto, on_resumen=resumenes.append
                )
        except Exception as exc:   # noqa: BLE001 — fallo del provisioning → estado=error sanitizado
            await estado.marcar(job_id, "error", slug=slug, error=_sanitizar_error(exc))
            _log_provision.error("provision_job_error", job_id=job_id, slug=slug, tipo=type(exc).__name__)
            return "error"

        await estado.marcar(
            job_id, "ok", slug=slug, empresa_id=empresa_id,
            resumen=resumenes[0] if resumenes else "",
        )
        _log_provision.info("provision_job_ok", job_id=job_id, slug=slug, empresa_id=empresa_id)
        return "ok"
    finally:
        await redis.aclose()


async def emitir_documento(ctx: dict, tenant_id: int, factura_id: int) -> str:
    """Emite la factura y traduce la `Decision` (E4b-1) a la semántica del worker ARQ.

    `servicio = await ctx["crear_servicio"](tenant_id)` (seam inyectado por `on_startup`);
    `decision = await servicio.emitir(factura_id)`. reintentar → `Retry` con backoff; dead_letter →
    log + "dead_letter"; si no → `decision.estado`. Nunca propaga otra excepción (`emitir` no lanza).
    """
    servicio = await ctx["crear_servicio"](tenant_id)
    decision = await servicio.emitir(factura_id)
    if decision.reintentar:
        raise Retry(defer=_backoff(ctx.get("job_try", 1)))
    if decision.dead_letter:
        log.warning("emision_dead_letter", tenant_id=tenant_id, factura_id=factura_id)
        return "dead_letter"
    if decision.estado == "aceptada":
        await _encolar_descarga(ctx, tenant_id, factura_id)
    return decision.estado


async def _encolar_descarga(ctx: dict, tenant_id: int, factura_id: int) -> None:
    """Encola el archivado del XML (D7.3) si hay pool ARQ en el `ctx`.

    ARQ inyecta `ctx["redis"]` (ArqRedis) en runtime; en tests/smoke sin Redis se omite (no lanza), igual
    que el resto de seams del worker. El webhook y el reconciliador encolan este mismo job al aceptar."""
    redis = ctx.get("redis")
    if redis is not None:
        await redis.enqueue_job("descargar_documento", tenant_id, factura_id)


async def descargar_documento(ctx: dict, tenant_id: int, factura_id: int) -> str:
    """Archiva el XML técnico de una factura aceptada (histórico fiscal 5 años, D7.3).

    Reusa el seam `crear_servicio` y el backoff de la emisión: `descargar_documento` del servicio es
    idempotente (no re-descarga si ya hay XML) y solo devuelve False en fallo de transporte → `Retry`."""
    servicio = await ctx["crear_servicio"](tenant_id)
    ok = await servicio.descargar_documento(factura_id)
    if not ok:
        raise Retry(defer=_backoff(ctx.get("job_try", 1)))
    return "archivado"


async def procesar_webhook_matias(ctx: dict, tenant_id: int, recibido_id: int) -> str:
    """Aplica un webhook MATIAS ya registrado (D7.1) a la factura: estado + SSE + archivado si quedó aceptada.

    El webhook (`POST /webhooks/matias/{token}`) solo registra y encola; aquí el worker corre el cambio
    de estado sobre la base del tenant. Si la factura quedó `aceptada`, encola el archivado del XML."""
    servicio = await ctx["crear_servicio"](tenant_id)
    accion, factura_id = await servicio.procesar_webhook(recibido_id)
    if accion == "aceptada" and factura_id is not None:
        await _encolar_descarga(ctx, tenant_id, factura_id)
    return accion
