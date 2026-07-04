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
from apps.wa.webhook import crear_router_wa
from apps.wa.wiring import construir_wa_deps
from core.observability import init_sentry
from core.tenancy.middleware import TenantMiddleware
from apps.api.cors import AUTH_CORS_PATHS, ScopedCORSMiddleware
from modules.admin.router import router as admin_router
from modules.agenda.router import router as agenda_router
from modules.auth.login_email import router as auth_email_router
from modules.auth.password_reset import router as auth_reset_router
from modules.auth.router import router as auth_router
from modules.bancos.router import router as bancos_router
from modules.caja.router import gastos_router, router as caja_router
from modules.clientes.router import router as clientes_router
from modules.cobranza.router import router as cobranza_router
from modules.compras.router import router as compras_router
from modules.compras_fiscal.router import router as compras_fiscal_router
from modules.config.router import router as config_router
from modules.conversaciones.router import router as conversaciones_router
from modules.cotizaciones.router import router as cotizaciones_router
from modules.devoluciones.router import router as devoluciones_router
from modules.facturacion.router import router as facturacion_router
from modules.facturacion.webhook import crear_router_matias
from modules.facturacion.webhook_wiring import construir_webhook_matias_deps
from modules.faq.router import router as faq_router
from modules.pagar.router import router as pagar_router
from modules.pagos.router import router as pagos_router
from modules.pedidos.router import router as pedidos_router
from modules.fiados.router import router as fiados_router
from modules.inventario.router import router as inventario_router, router_catalogo as catalogo_router
from modules.postventa.router import router as postventa_router
from modules.proveedores.router import router as proveedores_router
from modules.reportes_agente.router import router as reportes_agente_router
from modules.reportes.router import router as reportes_router
from modules.reservas.router import router as reservas_router
from modules.retenciones.router import router as retenciones_router
from modules.ventas.router import router as ventas_router

log = get_logger("api")

# dist del dashboard (build de Vite): apps/api/main.py → repo root → dashboard/dist.
DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_sentry("api")
    configure_logging()
    log.info("api_arranque")
    # Pool ARQ (perezoso, sobre Redis): el endpoint de facturación y el canal WhatsApp encolan aquí.
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    # Deps del webhook de WhatsApp (Kapso): resolver de control DB, dedup Redis y eco encolado.
    app.state.wa_deps = construir_wa_deps(app.state.arq_pool)
    # Deps del webhook de MATIAS (D7.1): resolver token→empresa+secret, idempotencia en tenant, encolar.
    app.state.matias_webhook_deps = construir_webhook_matias_deps(app.state.arq_pool)
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
    # CORS quirúrgico (plan §3): SOLO las rutas públicas de auth, SOLO desde el origin de la landing.
    # Se añade DESPUÉS del TenantMiddleware → queda MÁS EXTERNO: el preflight OPTIONS se responde antes
    # de resolver tenant, y el resto de la API jamás recibe headers CORS. Origins por settings (env).
    app.add_middleware(
        ScopedCORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["POST"],
        allow_paths=AUTH_CORS_PATHS,
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(auth_email_router, prefix="/api/v1")   # login email/contraseña (ADR 0009)
    app.include_router(auth_reset_router, prefix="/api/v1")   # set-password / reset por token (ADR 0009)
    app.include_router(admin_router, prefix="/api/v1")        # panel super-admin, cross-tenant (ADR 0010)
    app.include_router(ventas_router, prefix="/api/v1")
    app.include_router(devoluciones_router, prefix="/api/v1")  # devoluciones + nota crédito (ADR 0026)
    app.include_router(catalogo_router, prefix="/api/v1")    # /productos* — feature `ventas` (ADR 0021)
    app.include_router(inventario_router, prefix="/api/v1")  # /inventario/* — feature `inventario`
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
    app.include_router(agenda_router, prefix="/api/v1")
    app.include_router(conversaciones_router, prefix="/api/v1")
    app.include_router(faq_router, prefix="/api/v1")
    app.include_router(cobranza_router, prefix="/api/v1")   # página Cartera (ADR 0015)
    app.include_router(pedidos_router, prefix="/api/v1")    # kanban Pedidos (ADR 0016)
    app.include_router(cotizaciones_router, prefix="/api/v1")  # cotizaciones WA (ADR 0017)
    app.include_router(pagos_router, prefix="/api/v1")         # cobros (ADR 0013)
    app.include_router(pagar_router, prefix="/api/v1")         # cuentas por pagar (ADR 0019)
    app.include_router(bancos_router, prefix="/api/v1")        # conciliación bancaria (ADR 0028)
    app.include_router(postventa_router, prefix="/api/v1")     # postventa (plan §2.6)
    app.include_router(reservas_router, prefix="/api/v1")      # reservas por noches (pack_reservas)
    app.include_router(reportes_agente_router, prefix="/api/v1")  # analítica del dueño (Ola 3 §11)
    app.include_router(retenciones_router, prefix="/api/v1")   # retenciones/INC (ADR 0027)
    # Webhook único de WhatsApp (Kapso): NO va bajo /api/ (no es por-empresa; resuelve el tenant por
    # phone_number_id). El TenantMiddleware lo deja pasar (solo /api/ es por-empresa).
    app.include_router(crear_router_wa())
    # Webhook de MATIAS (D7.1): `/webhooks/matias/{token}`, fuera de /api/ (sin JWT; protegido por firma
    # + token del registro). Resuelve la empresa por el token, jamás por el payload.
    app.include_router(crear_router_matias())

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
