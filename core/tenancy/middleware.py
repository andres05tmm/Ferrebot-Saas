"""TenantMiddleware: resuelve la empresa antes de tocar datos (regla de multitenancy #1).

Si no resuelve a una empresa ACTIVA -> 404/403 y no se abre ninguna sesión de negocio.
Liga request_id y tenant_id al contexto de logging (regla #6).

Solo las rutas del API (`/api/`) son por-empresa: el SPA del dashboard (HTML/assets/rutas de
cliente) se sirve igual para todos y resuelve su empresa después, vía `GET /api/v1/config`. Por eso
todo path que NO empiece por `/api/` (más los públicos de infra) pasa sin resolver tenant.
"""
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.db.session import get_control_db
from core.logging import request_id_var, tenant_id_var
from core.tenancy.cache import control_cache
from core.tenancy.control_repo import resolve_tenant_by_slug
from core.tenancy.resolver import resolve_slug

# Rutas que no requieren empresa (salud, raíz, docs).
_PUBLIC_PATHS = frozenset({"/health", "/ready", "/", "/docs", "/openapi.json", "/redoc"})

# Auth SIN tenant resuelto (login real, ADR 0009): el login por email/contraseña ocurre sobre el link
# compartido, ANTES de conocer la empresa (el tenant sale del usuario). No resuelve tenant aquí; el
# endpoint lo deriva de la identidad. (El login Telegram /auth/login SÍ requiere tenant: no va aquí.)
_AUTH_SIN_TENANT = frozenset({"/api/v1/auth/login/password"})


class TenantMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        rid_token = request_id_var.set(request.headers.get("x-request-id") or uuid.uuid4().hex)
        tid_token = tenant_id_var.set(None)
        try:
            # Solo /api/ es por-empresa; el resto (SPA, infra) y el login sin-tenant no resuelven tenant.
            if (
                request.url.path in _PUBLIC_PATHS
                or request.url.path in _AUTH_SIN_TENANT
                or not request.url.path.startswith("/api/")
            ):
                await self.app(scope, receive, send)
                return
            tenant = await self._resolve(request)
            if tenant is None:
                await self._deny(scope, receive, send, 404, "Empresa no encontrada")
                return
            if tenant.estado != "activa":
                await self._deny(scope, receive, send, 403, "Empresa inactiva")
                return
            tenant_id_var.set(tenant.id)
            scope["state"] = scope.get("state", {})
            request.state.tenant = tenant
            await self.app(scope, receive, send)
        finally:
            request_id_var.reset(rid_token)
            tenant_id_var.reset(tid_token)

    async def _resolve(self, request: Request):
        slug = resolve_slug(request)
        if not slug:
            return None
        cached = control_cache.get(slug)
        if cached is not None:
            return cached
        async for session in get_control_db():
            tenant = await resolve_tenant_by_slug(session, slug)
            if tenant is not None:
                control_cache.set(tenant)
            return tenant

    @staticmethod
    async def _deny(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        response = JSONResponse({"detail": detail}, status_code=status)
        await response(scope, receive, send)
