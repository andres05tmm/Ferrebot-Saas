"""Resuelve el slug de la empresa desde el request (tenancy.md §1).

Orden: subdominio `slug.BASE_DOMAIN` -> header `X-Tenant-Slug` -> claim `tenant` del JWT.
El header y el claim cubren el caso local (host = localhost, sin subdominio real).
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
    return None
