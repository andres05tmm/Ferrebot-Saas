"""Servicio API (FastAPI). Monta el middleware de tenant y los routers de dominio."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.db.engine_cache import engine_cache
from core.events import event_hub
from core.logging import configure_logging, get_logger
from core.tenancy.middleware import TenantMiddleware
from modules.caja.router import gastos_router, router as caja_router
from modules.fiados.router import router as fiados_router
from modules.inventario.router import router as inventario_router
from modules.ventas.router import router as ventas_router

log = get_logger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    log.info("api_arranque")
    yield
    await event_hub.dispose_all()
    await engine_cache.dispose_all()
    log.info("api_apagado")


def create_app() -> FastAPI:
    app = FastAPI(title="FerreBot SaaS API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TenantMiddleware)
    app.include_router(ventas_router, prefix="/api/v1")
    app.include_router(inventario_router, prefix="/api/v1")
    app.include_router(caja_router, prefix="/api/v1")
    app.include_router(gastos_router, prefix="/api/v1")
    app.include_router(fiados_router, prefix="/api/v1")

    @app.get("/health", tags=["infra"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", tags=["infra"])
    async def ready() -> dict:
        return {"status": "ready"}

    return app


app = create_app()
