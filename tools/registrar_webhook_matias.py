"""Registrar el webhook de MATIAS de un tenant (D7.1 del ADR 0012) y guardar el secret CIFRADO.

Paso operativo de onboarding (como dar de alta la resolución): da de alta en MATIAS la URL de callback
`/webhooks/matias/{token}` (token único por empresa) y persiste el registro:
- `webhooks_matias` (control DB): token + callback (no secretos).
- `secretos_empresa` (control DB): el SECRET de firma que MATIAS devuelve UNA vez, CIFRADO (security.md).

Resuelve la empresa por slug, descifra sus credenciales MATIAS (login), genera el token, registra el
webhook (`POST {url}/ubl2.1/webhooks`) y guarda todo. Idempotente por empresa (re-registrar reemplaza
token y secret). El secret jamás se imprime.

Uso:
    python -m tools.registrar_webhook_matias <slug> <url> [--callback-base https://app.midominio.co]
      <url>            base de la API MATIAS para el POST de registro (p. ej. https://api-v2.matias-api.com)
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


async def registrar(slug: str, matias_url: str, callback_base: str) -> str:
    """Registra el webhook en MATIAS y persiste token + secret cifrado. Devuelve la URL de callback."""
    settings = get_settings()
    master = settings.secrets_master_key
    async with control_session() as cs:
        tenant = await resolve_tenant_by_slug(cs, slug)
        if tenant is None:
            raise ValueError(f"empresa '{slug}' no existe")
        cred, _config = await cargar_config_matias(cs, master, tenant.id)

    token = secrets.token_urlsafe(32)
    callback_url = f"{callback_base}/webhooks/matias/{token}"
    registro_url = f"{matias_url.rstrip('/')}/ubl2.1/webhooks"

    cliente = MatiasClient(cred)
    try:
        secret = await cliente.registrar_webhook(callback_url, events=_EVENTOS, registro_url=registro_url)
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
    parser.add_argument("url", help="base de la API MATIAS (para POST {url}/ubl2.1/webhooks)")
    parser.add_argument("--callback-base", default=None,
                        help="base pública de este servicio (default: https://{base_domain})")
    args = parser.parse_args(argv)

    callback = asyncio.run(registrar(args.slug, args.url, _callback_base(args.callback_base)))
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
