"""Servicio API (FastAPI). Monta el middleware de tenant y los routers de dominio."""
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import get_settings
from core.db.engine_cache import engine_cache
from core.db.session import _control
from core.events import event_hub
from core.logging import configure_logging, get_logger
from core.tenancy.middleware import TenantMiddleware
from modules.caja.router import gastos_router, router as caja_router
from modules.facturacion.router import router as facturacion_router
from modules.fiados.router import router as fiados_router
from modules.inventario.router import router as inventario_router
from modules.ventas.router import router as ventas_router

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("api_arranque")
    # Pool ARQ (perezoso, sobre Redis): el endpoint de facturación encola la emisión aquí.
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    yield
    await app.state.arq_pool.aclose()
    await event_hub.dispose_all()
    await engine_cache.dispose_all()
    log.info("api_apagado")


@dataclass(frozen=True, slots=True)
class ResultadoListo:
    """Resultado del readiness: si todas las dependencias responden y el estado de cada check."""
    listo: bool
    checks: dict[str, str]


async def chequear_listo(
    arq_pool,
    control_sessionmaker: Callable[[], AbstractAsyncContextManager],
) -> ResultadoListo:
    """Verifica las dependencias (control DB con SELECT 1, Redis con ping sobre el pool ARQ).

    Función pura y testeable: recibe el pool ARQ y el proveedor de sesión de control como
    parámetros (no lee app.state), para poder inyectar dobles en pruebas. No abre engines nuevos.
    """
    checks: dict[str, str] = {}
    try:
        async with control_sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        checks["control_db"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness reporta cualquier fallo como caída
        log.error("ready_control_db_caido", error=str(exc))
        checks["control_db"] = "error"
    try:
        await arq_pool.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        log.error("ready_redis_caido", error=str(exc))
        checks["redis"] = "error"
    return ResultadoListo(listo=all(v == "ok" for v in checks.values()), checks=checks)


async def evaluar_listo(request: Request) -> ResultadoListo:
    """Dependencia FastAPI: cablea el pool ARQ del lifespan y la sesión de control reutilizados."""
    return await chequear_listo(request.app.state.arq_pool, _control())


def create_app() -> FastAPI:
    app = FastAPI(title="FerreBot SaaS API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TenantMiddleware)
    app.include_router(ventas_router, prefix="/api/v1")
    app.include_router(inventario_router, prefix="/api/v1")
    app.include_router(caja_router, prefix="/api/v1")
    app.include_router(gastos_router, prefix="/api/v1")
    app.include_router(fiados_router, prefix="/api/v1")
    app.include_router(facturacion_router, prefix="/api/v1")

    @app.get("/health", tags=["infra"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", tags=["infra"])
    async def ready(resultado: ResultadoListo = Depends(evaluar_listo)):
        if not resultado.listo:
            return JSONResponse(
                {"status": "not_ready", "checks": resultado.checks}, status_code=503
            )
        return {"status": "ready", "checks": resultado.checks}

    return app


app = create_app()
