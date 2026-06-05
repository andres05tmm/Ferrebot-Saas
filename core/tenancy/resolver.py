"""Resuelve el slug de la empresa desde el request (tenancy.md §1).

Orden: subdominio `slug.BASE_DOMAIN` -> header `X-Tenant-Slug` -> claim `tenant` del JWT -> empresa por
defecto (`DEFAULT_TENANT_SLUG`, opt-in). El header y el claim cubren el caso local (host = localhost, sin
subdominio real). El default es el ÚLTIMO recurso para despliegues SINGLE-TENANT sin dominio propio (el
dominio que da Railway, sin subdominio): si no está seteado se devuelve `None` como siempre, así que el
aislamiento multi-tenant queda intacto y las señales explícitas (subdominio/header/JWT) SIEMPRE ganan.
"""
from starlette.requests import HTTPConnection

from core.auth.jwt import decode_token_optional
from core.config import get_settings


def _slug_from_host(host: str, base_domain: str) -> str | None:
    host = host.split(":")[0].lower()
    base = base_domain.lower()
    if host in (base, "localhost", "127.0.0.1") or not host.endswith("." + base):
        return None
    label = host[: -(len(base) + 1)]
    return label or None


def resolve_slug(conn: HTTPConnection) -> str | None:
    settings = get_settings()
    host = conn.headers.get("host", "")
    slug = _slug_from_host(host, settings.base_domain)
    if slug:
        return slug
    header_slug = conn.headers.get("x-tenant-slug")
    if header_slug:
        return header_slug.strip().lower()
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        claims = decode_token_optional(auth[7:])
        if claims and claims.get("tenant"):
            return str(claims["tenant"])
    # Último recurso (opt-in, solo single-tenant): empresa por defecto cuando NADA explícito resolvió.
    # Sin `DEFAULT_TENANT_SLUG` → None (igual que antes); las señales explícitas ya retornaron arriba.
    if settings.default_tenant_slug:
        return settings.default_tenant_slug.strip().lower()
    return None
