"""Runner de migraciones a TODAS las empresas (tenancy.md §7).

Lee empresas del control DB, aplica `upgrade head` a cada base. Si una falla, continúa con
las demás y reporta al final (no aborta todo). Idealmente corre como job ARQ en el deploy.
"""
import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.crypto import decrypt
from core.db.urls import to_libpq
from core.logging import configure_logging, get_logger
from tools._alembic import upgrade_tenant

log = get_logger("migrate_tenants")


def _empresas(control_url: str) -> list[dict]:
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as conn:
        return conn.execute(
            """SELECT e.id, e.slug, e.estado, t.connection_url_cifrada
               FROM empresas e JOIN tenant_databases t ON t.empresa_id = e.id
               WHERE e.estado IN ('activa', 'suspendida')
               ORDER BY e.id"""
        ).fetchall()


def migrate_all() -> tuple[list[str], dict[str, str]]:
    """Devuelve (ok_slugs, {slug: error}). No lanza si una empresa falla."""
    settings = get_settings()
    ok: list[str] = []
    fallidas: dict[str, str] = {}
    for emp in _empresas(settings.control_database_url):
        slug = emp["slug"]
        try:
            url = decrypt(emp["connection_url_cifrada"], settings.secrets_master_key)
            upgrade_tenant(url)
            ok.append(slug)
            log.info("tenant_migrado", slug=slug)
        except Exception as exc:  # noqa: BLE001 - se reporta y se continúa
            fallidas[slug] = str(exc)
            log.error("tenant_migracion_fallida", slug=slug, error=str(exc))
    return ok, fallidas


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Migrar todas las empresas.").parse_args(argv)
    configure_logging()
    ok, fallidas = migrate_all()
    print(f"OK: {len(ok)} | Fallidas: {len(fallidas)}")
    for slug, err in fallidas.items():
        print(f"  - {slug}: {err}")
    return 1 if fallidas else 0


if __name__ == "__main__":
    sys.exit(main())
