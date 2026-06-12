"""CORS QUIRÚRGICO para las rutas públicas de auth (plan Melquiadez §3, M4).

La landing en `melquiadez.com` (Cloudflare) hace POST cross-origin al login de la API en
`app.melquiadez.com`. Eso exige CORS — pero SOLO para las rutas de auth públicas y SOLO desde el
origin de la landing. JAMÁS abrir CORS en toda la API (regla del plan §9 y de seguridad).

El `CORSMiddleware` de Starlette es GLOBAL (se aplica a TODA request, sin scoping por ruta). Para no
filtrar headers CORS al resto de la API, lo ENVOLVEMOS (no reimplementamos el protocolo): delegamos al
`CORSMiddleware` real solo cuando el path está en la allow-list de auth; cualquier otro path pasa
directo a la app interna y nunca ve un header CORS. Va como middleware MÁS EXTERNO (se añade después
del TenantMiddleware en `create_app`) para que el preflight OPTIONS se responda antes de resolver
tenant.

Origins por settings/env (`cors_allow_origins`), nunca hardcodeados aquí.
"""
from __future__ import annotations

from collections.abc import Iterable

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# Rutas públicas de auth que la landing llama cross-origin. Paths COMPLETOS (incluyen el prefijo
# /api/v1 con que se montan los routers en create_app). Cualquier otra ruta queda fuera de CORS.
AUTH_CORS_PATHS = frozenset({
    "/api/v1/auth/login/password",   # login real email/contraseña (ADR 0009)
    "/api/v1/auth/reset/solicitar",  # "olvidé mi contraseña" (genera el token de reset)
})


class ScopedCORSMiddleware:
    """Aplica CORS SOLO a `allow_paths`; el resto de la app no recibe ningún header CORS.

    Envuelve el `CORSMiddleware` de Starlette (battle-tested) en vez de reimplementar el preflight.
    Para paths fuera de la allow-list, salta el CORS por completo y llama a la app interna.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allow_origins: Iterable[str],
        allow_methods: Iterable[str],
        allow_paths: Iterable[str],
    ) -> None:
        self.app = app
        self.allow_paths = frozenset(allow_paths)
        # `allow_headers=["*"]` → el preflight refleja los headers pedidos (p. ej. content-type del JSON).
        # Sin credenciales: el handoff viaja por fragmento de URL, no por cookies (plan §3).
        self.cors = CORSMiddleware(
            app,
            allow_origins=list(allow_origins),
            allow_methods=list(allow_methods),
            allow_headers=["*"],
            allow_credentials=False,
            max_age=600,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in self.allow_paths:
            await self.cors(scope, receive, send)
        else:
            await self.app(scope, receive, send)
