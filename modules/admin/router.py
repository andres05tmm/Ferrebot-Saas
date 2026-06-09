"""Panel super-admin: API de plataforma (ADR 0010 §D2/§B3). Opera CROSS-TENANT sobre el control DB.

Todas las rutas van bajo `/api/v1/admin`, **exentas del TenantMiddleware** (no son por-empresa; ver
core/tenancy/middleware) y **gateadas por `require_platform`** (super_admin + scope=platform): solo una
identidad de PLATAFORMA entra; un admin/vendedor de tenant → 403. El super-admin lee/escribe el control
DB y encola el provisioning en el worker; nunca abre la base de un tenant directamente desde el API.

Endpoints:
- GET  /admin/tenants                       — lista de empresas (B1).
- POST /admin/tenants                       — valida el manifiesto server-side y ENCOLA el provisioning (B2 job).
- GET  /admin/jobs/{job_id}                 — estado del job de provisioning.
- PUT  /admin/tenants/{slug}/features       — prende/apaga una feature (reusa tools.set_feature).
- POST /admin/tenants/{slug}/identidad-admin — crea/re-emite la identidad admin del tenant + enlace set-password.

Disciplina de secretos: el manifiesto/secretos JAMÁS al log; el token de set-password se devuelve al
operador (HTTPS) pero nunca se loguea.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from apps.worker.jobs import EstadoProvision
from core.auth import require_platform
from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.control_repo import listar_tenants
from tools.grandfather_identidad import grandfather
from tools.manifest import ErrorManifiesto, validar
from tools.manifest.schema import Manifiesto
from tools.set_feature import set_feature

log = get_logger("admin")

# Gate de plataforma a nivel de router: cada ruta exige una identidad de plataforma (super_admin +
# scope=platform). Cubre toda /admin/* (ADR 0010 §D2).
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_platform)])


# ── Seams inyectables (cola ARQ + estado en Redis) ───────────────────────────
class Enqueuer(Protocol):
    """Puerto de cola: encola un job ARQ. En prod = pool ARQ del lifespan; en tests, un fake."""

    async def enqueue(self, job: str, *args) -> None: ...


class _ArqEnqueuer:
    """Adaptador sobre el pool ARQ del lifespan (`app.state.arq_pool`)."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def enqueue(self, job: str, *args) -> None:
        await self._pool.enqueue_job(job, *args)


async def get_enqueuer(request: Request) -> Enqueuer:
    return _ArqEnqueuer(request.app.state.arq_pool)


def _cliente_redis(url: str):
    """Cliente Redis async (perezoso): importa `redis.asyncio` al invocar (patrón de modules.auth)."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


async def get_estado_provision() -> AsyncIterator[EstadoProvision]:
    """Store del estado de los jobs de provisioning (Redis). Cierra el cliente al terminar el request."""
    settings = get_settings()
    redis = _cliente_redis(settings.redis_url)
    try:
        yield EstadoProvision(redis, settings.provision_estado_ttl_segundos)
    finally:
        await redis.aclose()


# ── Modelos de E/S ───────────────────────────────────────────────────────────
class TenantOut(BaseModel):
    """Una empresa vista desde el panel super-admin (control DB)."""

    id: int
    slug: str
    nombre: str
    estado: str
    plan: str | None = None
    features: list[str] = []
    wa_numero: str | None = None


class EncolarOut(BaseModel):
    job_id: str


class EstadoJobOut(BaseModel):
    """Estado de un job de provisioning (EstadoProvision)."""

    job_id: str | None = None
    estado: str
    slug: str | None = None
    resumen: str | None = None
    error: str | None = None
    empresa_id: int | None = None


class FeatureToggle(BaseModel):
    feature: str
    habilitada: bool


class FeaturesOut(BaseModel):
    slug: str
    features: list[str]


class IdentidadAdminIn(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email_valido(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("email no parece válido")
        return v


class IdentidadAdminOut(BaseModel):
    identidad_id: int
    # Token del enlace de set-password para entrega manual al cliente (HTTPS al operador; nunca al log).
    set_password_token: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/tenants", response_model=list[TenantOut])
async def get_tenants() -> list[TenantOut]:
    """Lista las empresas de la plataforma (slug, nombre, estado, plan, features efectivas, número WA).

    El gate `require_platform` está a nivel de router (cubre toda /admin/*)."""
    async with control_session() as cs:
        resumenes = await listar_tenants(cs)
    return [
        TenantOut(
            id=t.id, slug=t.slug, nombre=t.nombre, estado=t.estado, plan=t.plan,
            features=list(t.features), wa_numero=t.wa_numero,
        )
        for t in resumenes
    ]


@router.post("/tenants", response_model=EncolarOut, status_code=status.HTTP_202_ACCEPTED)
async def crear_tenant(
    manifiesto: Manifiesto,
    enqueuer: Enqueuer = Depends(get_enqueuer),
    estado: EstadoProvision = Depends(get_estado_provision),
) -> EncolarOut:
    """Valida el manifiesto SERVER-SIDE y ENCOLA el job de provisioning (no provisiona en el request).

    El esquema (`Manifiesto`) ya impone forma + slug estricto (422 si falla); aquí se corre la validación
    SEMÁNTICA (`validar`: catálogo, dependencias, coherencia) antes de encolar — nunca se confía en el
    cliente. Marca el estado en `encolado` y devuelve `{job_id}`. El manifiesto/secretos jamás se loguean.
    """
    try:
        validar(manifiesto)
    except ErrorManifiesto as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    slug = manifiesto.identidad.slug
    job_id = uuid.uuid4().hex
    await estado.marcar(job_id, "encolado", slug=slug)
    await enqueuer.enqueue("provisionar_tenant", manifiesto.model_dump(mode="json"), job_id)
    log.info("provision_encolado", job_id=job_id, slug=slug)   # nunca el manifiesto/secretos
    return EncolarOut(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=EstadoJobOut)
async def get_job(job_id: str, estado: EstadoProvision = Depends(get_estado_provision)) -> EstadoJobOut:
    """Estado del job de provisioning: estado, slug, resumen, error (404 si no existe/expiró)."""
    data = await estado.obtener(job_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job no encontrado")
    return EstadoJobOut(
        job_id=data.get("job_id"), estado=data["estado"], slug=data.get("slug"),
        resumen=data.get("resumen"), error=data.get("error"), empresa_id=data.get("empresa_id"),
    )


@router.put("/tenants/{slug}/features", response_model=FeaturesOut)
async def put_feature(slug: str, body: FeatureToggle) -> FeaturesOut:
    """Prende/apaga una feature del tenant (reusa `tools.set_feature`: valida catálogo + dependencias).

    `set_feature` es sync (psycopg) → a un hilo para no bloquear el loop. Catálogo/dependencias/empresa
    inexistente → ValueError → 400 (mensaje del helper; sin secretos). Devuelve el set EFECTIVO resultante.
    """
    try:
        efectivas = await asyncio.to_thread(set_feature, slug, body.feature, body.habilitada)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return FeaturesOut(slug=slug, features=sorted(efectivas))


@router.post("/tenants/{slug}/identidad-admin", response_model=IdentidadAdminOut)
async def crear_identidad_admin(slug: str, body: IdentidadAdminIn) -> IdentidadAdminOut:
    """Crea/re-emite la identidad admin del tenant + su token de set-password (reusa A1, idempotente).

    Reusa `tools.grandfather_identidad.grandfather` (sync): resuelve empresa+admin del tenant, hace upsert
    de la identidad por email y emite el token. El email se RECIBE (no se inventa). empresa/admin
    inexistentes → 404. El token se devuelve al operador (HTTPS) pero NUNCA se loguea.
    """
    try:
        identidad_id, token = await asyncio.to_thread(grandfather, slug, body.email)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return IdentidadAdminOut(identidad_id=identidad_id, set_password_token=token)
