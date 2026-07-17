"""Configura el canal Telegram público (@SiriusBot) de un tenant: token cifrado + registro del webhook.

    python -m tools.set_tg_publico <slug> <bot_token> <webhook_url_base>

Hace tres cosas (control DB + Bot API), idempotentes:
  (a) cifra y guarda el token del bot en `secretos_empresa` (clave `tg_publico_bot_token`) con
      `SECRETS_MASTER_KEY`;
  (b) genera un secret para el webhook y lo guarda cifrado (clave `tg_publico_webhook_secret`);
  (c) llama `setWebhook` de la Bot API con `url = {base}/tg-publico/{slug}` y `secret_token = <secret>`
      (el mismo secret que valida el webhook entrante).

Se genera un secret NUEVO en cada corrida y se re-registra: así el secret guardado y el registrado
quedan SIEMPRE en sync. El token de BotFather es SECRETO: nunca al log ni a stdout (solo se confirma el
registro y la URL, que no es secreta). Espeja `tools/set_gmail_token.py` (mismo patrón de cifrado).
"""
from __future__ import annotations

import argparse
import secrets
import sys

import psycopg
from psycopg.rows import dict_row

from apps.tg_publico.repos import CLAVE_BOT_TOKEN, CLAVE_WEBHOOK_SECRET
from core.config import get_settings
from core.crypto import encrypt_split
from core.db.urls import to_libpq
from core.logging import configure_logging, get_logger

log = get_logger("set_tg_publico")


def _guardar_secreto(conn: psycopg.Connection, empresa_id: int, clave: str, valor: str, master: str) -> None:
    """Upsert cifrado de un secreto del tenant en `secretos_empresa` (valor_cifrado + nonce)."""
    cifrado, nonce = encrypt_split(valor, master)
    conn.execute(
        "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
        "VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (empresa_id, clave) DO UPDATE SET valor_cifrado=EXCLUDED.valor_cifrado, "
        "nonce=EXCLUDED.nonce",
        (empresa_id, clave, cifrado, nonce),
    )


def _set_webhook(bot_token: str, url: str, secret: str) -> None:
    """Registra el webhook en Telegram con el secret-token. httpx perezoso (import dentro de la función)."""
    import httpx

    resp = httpx.post(
        f"https://api.telegram.org/bot{bot_token}/setWebhook",
        json={"url": url, "secret_token": secret, "allowed_updates": ["message"]},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"setWebhook falló: {data.get('description')}")


def configurar(slug: str, bot_token: str, webhook_url_base: str) -> str:
    """Guarda el token + secret cifrados y registra el webhook. Devuelve la URL registrada (no secreta)."""
    settings = get_settings()
    master = settings.secrets_master_key
    secret = secrets.token_urlsafe(32)
    with psycopg.connect(to_libpq(settings.control_database_url), row_factory=dict_row) as conn:
        empresa = conn.execute("SELECT id FROM empresas WHERE slug=%s", (slug,)).fetchone()
        if empresa is None:
            raise ValueError(f"empresa '{slug}' no existe")
        empresa_id = empresa["id"]
        _guardar_secreto(conn, empresa_id, CLAVE_BOT_TOKEN, bot_token, master)
        _guardar_secreto(conn, empresa_id, CLAVE_WEBHOOK_SECRET, secret, master)
        conn.commit()
    url = f"{webhook_url_base.rstrip('/')}/tg-publico/{slug}"
    _set_webhook(bot_token, url, secret)
    log.info("tg_publico_configurado", slug=slug, empresa_id=empresa_id, url=url)
    return url


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Configura el canal Telegram público de un tenant")
    parser.add_argument("slug", help="slug de la empresa (control DB)")
    parser.add_argument("bot_token", help="token del bot de BotFather (SECRETO)")
    parser.add_argument("webhook_url_base", help="base pública del webhook, p. ej. https://<túnel>")
    args = parser.parse_args(argv)
    try:
        url = configurar(args.slug, args.bot_token, args.webhook_url_base)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"OK. webhook registrado en: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
