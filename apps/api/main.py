"""Servicio API (FastAPI). Monta el middleware de tenant, los routers de dominio y el SPA."""
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from core.config import get_settings
from core.db.engine_cache import engine_cache
from core.db.session import _control
from core.events import event_hub
from core.logging import configure_logging, get_logger
from core.observability import init_sentry
from core.tenancy.middleware import TenantMiddleware
from modules.auth.router import router as auth_router
from modules.caja.router import gastos_router, router as caja_router
from modules.clientes.router import router as clientes_router
from modules.compras.router import router as compras_router
from modules.compras_fiscal.router import router as compras_fiscal_router
from modules.config.router import router as config_router
from modules.facturacion.router import router as facturacion_router
from modules.fiados.router import router as fiados_router
from modules.inventario.router import router as inventario_router
from modules.proveedores.router import router as proveedores_router
from modules.reportes.router import router as reportes_router
from modules.ventas.router import router as ventas_router

log = get_logger("api")

# dist del dashboard (build de Vite): apps/api/main.py → repo root → dashboard/dist.
DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_sentry("api")
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


def mount_spa(app: FastAPI, dist_dir: Path) -> None:
    """Sirve el SPA del dashboard (build de Vite) con fallback a index.html (history API).

    Se registra DESPUÉS de los routers, así nunca intercepta /api/. Resiliente: si `dist_dir` no
    existe (sin `npm run build`), no monta estáticos y el catch-all responde 404 — la API queda intacta.
    """
    index = dist_dir / "index.html"
    assets = dist_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Defensa en profundidad: /api/ ya lo capturan los routers; aquí, 404 (nunca el index).
        if full_path.startswith("api/") or not index.is_file():
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Sirve un archivo real del build si lo piden (favicon, manifest, logo…), si no, el index (SPA).
        candidato = dist_dir / full_path if full_path else index
        if candidato.is_file() and candidato.resolve().is_relative_to(dist_dir.resolve()):
            return FileResponse(candidato)
        return FileResponse(index)


def create_app(spa_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="FerreBot SaaS API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TenantMiddleware)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(ventas_router, prefix="/api/v1")
    app.include_router(inventario_router, prefix="/api/v1")
    app.include_router(caja_router, prefix="/api/v1")
    app.include_router(gastos_router, prefix="/api/v1")
    app.include_router(fiados_router, prefix="/api/v1")
    app.include_router(facturacion_router, prefix="/api/v1")
    app.include_router(clientes_router, prefix="/api/v1")
    app.include_router(compras_router, prefix="/api/v1")
    app.include_router(compras_fiscal_router, prefix="/api/v1")
    app.include_router(proveedores_router, prefix="/api/v1")
    app.include_router(reportes_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")

    @app.api_route("/health", methods=["GET", "HEAD"], tags=["infra"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", tags=["infra"])
    async def ready(resultado: ResultadoListo = Depends(evaluar_listo)):
        if not resultado.listo:
            return JSONResponse(
                {"status": "not_ready", "checks": resultado.checks}, status_code=503
            )
        return {"status": "ready", "checks": resultado.checks}

    # SPA al final: el catch-all no debe sombrear ninguna ruta /api ni de infra.
    mount_spa(app, spa_dist or DASHBOARD_DIST)
    return app


app = create_app()
