"""Dependencias de auth para FastAPI: principal autenticado + control de rol.

El usuario debe pertenecer a la empresa resuelta (claim `tenant` == request.state.tenant.slug):
nunca un token de la empresa A opera sobre la empresa B (aislamiento, SECURITY.md).
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.auth.jwt import decode_token_optional
from core.auth.rbac import satisface

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    tenant: str | None   # None para una identidad de PLATAFORMA (super-admin, ADR 0010 §D2)
    rol: str
    scope: str = "tenant"   # "tenant" | "platform"

    @property
    def es_plataforma(self) -> bool:
        return self.scope == "platform"


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token")
    claims = decode_token_optional(creds.credentials)
    if not claims or "sub" not in claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")
    scope = claims.get("scope", "tenant")
    claim_tenant = claims.get("tenant")
    tenant = getattr(request.state, "tenant", None)
    # INVARIANTE (ADR 0010 §D2): si el request resolvió una empresa, el token DEBE traer su mismo `tenant`.
    # Un token de plataforma (sin claim `tenant`) jamás "pertenece" a una empresa resuelta → 403. Y como no
    # trae `tenant`, el resolver no resuelve ninguna empresa para las rutas de tenant → el middleware 404.
    if tenant is not None and claim_tenant != tenant.slug:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "El token no pertenece a esta empresa")
    return Principal(
        user_id=int(claims["sub"]), tenant=claim_tenant, rol=claims.get("rol", "vendedor"), scope=scope
    )


def require_role(rol_requerido: str):
    """Dependencia que exige un rol mínimo."""
    def _dep(user: Principal = Depends(get_current_user)) -> Principal:
        if not satisface(user.rol, rol_requerido):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Permisos insuficientes")
        return user
    return _dep


def require_platform(user: Principal = Depends(get_current_user)) -> Principal:
    """Gate de las rutas /admin/* (ADR 0010 §D2): identidad de PLATAFORMA (super_admin + scope=platform).

    Defensa en profundidad: además del rol super_admin, exige `scope == platform`. Hoy son equivalentes
    (super_admin ⇔ plataforma por el CHECK de la migración 0006 + el login), pero asentarlo deja el gate
    inequívoco: un token super_admin que NO fuera de plataforma jamás operaría el panel cross-tenant.
    """
    if not (user.es_plataforma and satisface(user.rol, "super_admin")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Se requiere una identidad de plataforma")
    return user


def get_filtro_efectivo(
    user: Principal = Depends(get_current_user),
    vendedor_id: int | None = Query(default=None),
) -> int | None:
    """Vendedor efectivo para acotar listados/reportes (auth-rbac.md).

    - vendedor → su propio `user_id` (IGNORA `?vendedor_id`: nunca ve lo de otro).
    - admin/super_admin → el `?vendedor_id` recibido: `None` = ve todo; un id = impersona a ese
      vendedor (selector de vendedor del dashboard).
    """
    if satisface(user.rol, "admin"):
        return vendedor_id
    return user.user_id
