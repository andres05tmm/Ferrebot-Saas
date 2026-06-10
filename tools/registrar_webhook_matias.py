"""Registrar el webhook de MATIAS de un tenant (D7.1 del ADR 0012) y guardar el secret CIFRADO.

Paso operativo de onboarding (como dar de alta la resolución): da de alta en MATIAS la URL de callback
`/webhooks/matias/{token}` (token único por empresa) y persiste el registro:
- `webhooks_matias` (control DB): token + callback (no secretos).
- `secretos_empresa` (control DB): el SECRET de firma que MATIAS devuelve UNA vez, CIFRADO (security.md).

Resuelve la empresa por slug, descifra sus credenciales MATIAS (login), genera el token, registra el
webhook (`POST {url}/api/ubl2.1/webhooks`) y guarda todo. Idempotente por empresa (re-registrar
reemplaza token y secret). El secret jamás se imprime.

Uso:
    python -m tools.registrar_webhook_matias <slug> <url> [--name NOMBRE] [--callback-base https://app.midominio.co]
      <url>            base de la API MATIAS CON /api (p. ej. https://api-v2.matias-api.com/api); se
                       tolera que venga sin /api y se normaliza. El registro va a {base}/ubl2.1/webhooks.
      --name           nombre del webhook en MATIAS (el endpoint lo EXIGE; default: nombre del tenant
                       o "FerreBot {slug}").
      --callback-base  base PÚBLICA de ESTE servicio (default: https://{base_domain} de settings)
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from core.config import get_settings
from core.db.session import control_session
from core.tenancy.control_repo import resolve_tenant_by_slug
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.matias_client import MatiasClient
from modules.facturacion.webhook_repo import guardar_registro_webhook

# Eventos a los que nos suscribimos (D7.1): aceptación / rechazo / anulación del documento.
_EVENTOS = ["document.accepted", "document.rejected", "document.voided"]


def _callback_base(arg: str | None) -> str:
    """Base pública de este servicio para el callback; default `https://{base_domain}` de settings."""
    if arg:
        return arg.rstrip("/")
    return f"https://{get_settings().base_domain}".rstrip("/")


def _registro_url(matias_url: str) -> str:
    """Normaliza la base MATIAS a la URL absoluta de registro `{base}/api/ubl2.1/webhooks`.

    Tolera que `matias_url` venga CON o SIN `/api` (no lo duplica): el endpoint exige el prefijo `/api`.
    """
    base = matias_url.rstrip("/")
    if not base.endswith("/api"):
        base = f"{base}/api"
    return f"{base}/ubl2.1/webhooks"


async def registrar(slug: str, matias_url: str, callback_base: str, name: str | None = None) -> str:
    """Registra el webhook en MATIAS y persiste token + secret cifrado. Devuelve la URL de callback."""
    settings = get_settings()
    master = settings.secrets_master_key
    async with control_session() as cs:
        tenant = await resolve_tenant_by_slug(cs, slug)
        if tenant is None:
            raise ValueError(f"empresa '{slug}' no existe")
        cred, _config = await cargar_config_matias(cs, master, tenant.id)

    nombre = name or tenant.nombre or f"FerreBot {slug}"
    token = secrets.token_urlsafe(32)
    callback_url = f"{callback_base}/webhooks/matias/{token}"
    registro_url = _registro_url(matias_url)

    cliente = MatiasClient(cred)
    try:
        secret = await cliente.registrar_webhook(
            callback_url, name=nombre, events=_EVENTOS, registro_url=registro_url
        )
    finally:
        await cliente.aclose()

    async with control_session() as cs:
        await guardar_registro_webhook(
            cs, master, tenant.id, token=token, callback_url=callback_url, secret=secret
        )
    return callback_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Registrar el webhook de MATIAS de un tenant (D7.1).")
    parser.add_argument("slug", help="slug de la empresa")
    parser.add_argument("url", help="base de la API MATIAS CON /api (p. ej. https://api-v2.matias-api.com/api); "
                                    "se tolera sin /api → POST {base}/api/ubl2.1/webhooks")
    parser.add_argument("--name", default=None,
                        help="nombre del webhook en MATIAS (obligatorio; default: nombre del tenant o 'FerreBot {slug}')")
    parser.add_argument("--callback-base", default=None,
                        help="base pública de este servicio (default: https://{base_domain})")
    args = parser.parse_args(argv)

    callback = asyncio.run(registrar(args.slug, args.url, _callback_base(args.callback_base), args.name))
    print(f"✓ webhook MATIAS registrado para '{args.slug}'")
    print(f"  callback: {callback}")
    print("  secret guardado cifrado en secretos_empresa (matias_webhook_secret)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
