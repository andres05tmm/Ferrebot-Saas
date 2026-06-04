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
from modules.facturacion.service import MAX_INTENTOS


async def on_startup(ctx: dict) -> None:
    """Inyecta `ctx['crear_servicio']`: dado tenant_id, arma el `FacturacionService` por empresa.

    TODO GREEN (E4b-2): resolver el tenant, `cargar_config_matias` del control DB, abrir la sesión del
    tenant y devolver un `FacturacionService(SqlFacturacionRepository(s), MatiasClient(cred), config)`.
    """
    ...   # TODO GREEN: ctx["crear_servicio"] = <factory por empresa>


class WorkerSettings:
    """Configuración del worker ARQ (functions, Redis, reintentos)."""

    functions = [emitir_documento]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = MAX_INTENTOS + 1
    on_startup = on_startup
