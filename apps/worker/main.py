"""Runtime del worker ARQ (emisión DIAN asíncrona). Smoke manual, no unit-test.

`WorkerSettings` es lo que arranca `arq apps.worker.main.WorkerSettings`. La lógica del job vive en
`apps.worker.jobs` (testeable sin Redis); aquí solo el cableado del runtime: Redis (perezoso, desde
REDIS_URL), tope de reintentos (`MAX_INTENTOS + 1`) y el seam `ctx["crear_servicio"]` que `on_startup`
arma con el wiring real por empresa.
"""
from __future__ import annotations

from arq.connections import RedisSettings

from apps.worker.jobs import emitir_documento
from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.tenancy.control_repo import resolve_tenant_by_id
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.matias_client import MatiasClient
from modules.facturacion.politica import Decision
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import MAX_INTENTOS, FacturacionService


class _ServicioEmision:
    """Adaptador por empresa: resuelve tenant + config (control DB) y emite sobre su base."""

    def __init__(self, tenant_id: int, master: str) -> None:
        self._tid = tenant_id
        self._master = master

    async def emitir(self, factura_id: int) -> Decision:
        async with control_session() as cs:
            tenant = await resolve_tenant_by_id(cs, self._tid)
            cred, config = await cargar_config_matias(cs, self._master, self._tid)
        decision: Decision | None = None
        async for s in tenant_session(tenant):   # commit al cerrar el generador (no `return` dentro)
            servicio = FacturacionService(SqlFacturacionRepository(s), MatiasClient(cred), config)
            decision = await servicio.emitir(factura_id)
        return decision


async def on_startup(ctx: dict) -> None:
    """Inyecta `ctx['crear_servicio']`: dado tenant_id, devuelve el adaptador de emisión por empresa."""
    master = get_settings().secrets_master_key

    async def crear_servicio(tenant_id: int) -> _ServicioEmision:
        return _ServicioEmision(tenant_id, master)

    ctx["crear_servicio"] = crear_servicio


class WorkerSettings:
    """Configuración del worker ARQ (functions, Redis, reintentos)."""

    functions = [emitir_documento]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = MAX_INTENTOS + 1
    on_startup = on_startup
