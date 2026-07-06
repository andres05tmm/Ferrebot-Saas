"""Registrar/rotar el buzón Gmail de ingesta Bancolombia de un tenant (control DB).

Hace dos cosas idempotentes:
  1. Cifra y guarda el `refresh_token` OAuth en `secretos_empresa` (clave `gmail_refresh_token_bancolombia`)
     con `SECRETS_MASTER_KEY` — reemplaza el endpoint de emergencia `POST .../token` del bot viejo.
  2. Da de alta (o actualiza) la fila `gmail_cuentas` con el `webhook_token`, el email y el topic Pub/Sub.

El `webhook_token` es el secreto de la URL `/webhooks/bancolombia/{token}` que fija la subscription de
Pub/Sub; si no se pasa, se genera uno nuevo. `client_id/secret` NO se guardan aquí (son de plataforma:
GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET en el entorno).

Uso:
    python -m tools.set_gmail_token <slug> --refresh-token <TOKEN> \
        [--email ferreteria.bancolombia@gmail.com] \
        [--pubsub-topic projects/PROJ/topics/bancolombia-notif] \
        [--webhook-token <TOKEN_OPACO>]
"""
from __future__ import annotations

import argparse
import secrets
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.crypto import encrypt_split
from core.db.urls import to_libpq
from modules.bancos.gmail.secretos import CLAVE_REFRESH


def registrar(
    slug: str, refresh_token: str, *, email: str | None = None, pubsub_topic: str | None = None,
    webhook_token: str | None = None,
) -> str:
    """Cifra el refresh_token y da de alta la cuenta Gmail. Devuelve el webhook_token efectivo."""
    settings = get_settings()
    cifrado, nonce = encrypt_split(refresh_token, settings.secrets_master_key)
    token = webhook_token or secrets.token_urlsafe(32)
    with psycopg.connect(to_libpq(settings.control_database_url), row_factory=dict_row) as conn:
        empresa = conn.execute("SELECT id FROM empresas WHERE slug=%s", (slug,)).fetchone()
        if empresa is None:
            raise ValueError(f"empresa '{slug}' no existe")
        empresa_id = empresa["id"]
        conn.execute(
            "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (empresa_id, clave) DO UPDATE SET valor_cifrado=EXCLUDED.valor_cifrado, "
            "nonce=EXCLUDED.nonce",
            (empresa_id, CLAVE_REFRESH, cifrado, nonce),
        )
        conn.execute(
            "INSERT INTO gmail_cuentas (empresa_id, proposito, email, webhook_token, pubsub_topic) "
            "VALUES (%s, 'bancolombia', %s, %s, %s) "
            "ON CONFLICT (empresa_id, proposito) DO UPDATE SET "
            "email=COALESCE(EXCLUDED.email, gmail_cuentas.email), "
            "pubsub_topic=COALESCE(EXCLUDED.pubsub_topic, gmail_cuentas.pubsub_topic), "
            "webhook_token=COALESCE(%s, gmail_cuentas.webhook_token), activo=true",
            (empresa_id, email, token, pubsub_topic, webhook_token),
        )
        # El webhook_token efectivo (el existente si no se forzó uno nuevo).
        fila = conn.execute(
            "SELECT webhook_token FROM gmail_cuentas WHERE empresa_id=%s AND proposito='bancolombia'",
            (empresa_id,),
        ).fetchone()
        conn.commit()
    return fila["webhook_token"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Registrar/rotar el buzón Gmail Bancolombia de un tenant")
    parser.add_argument("slug")
    parser.add_argument("--refresh-token", required=True)
    parser.add_argument("--email")
    parser.add_argument("--pubsub-topic")
    parser.add_argument("--webhook-token")
    args = parser.parse_args(argv)
    try:
        token = registrar(
            args.slug, args.refresh_token, email=args.email,
            pubsub_topic=args.pubsub_topic, webhook_token=args.webhook_token,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"OK. webhook_token = {token}")
    print(f"Configura la subscription de Pub/Sub con push endpoint: "
          f"https://<host>/webhooks/bancolombia/{token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
