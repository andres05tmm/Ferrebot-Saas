"""Runner de migraciones a TODAS las empresas (tenancy.md §7).

Lee empresas del control DB y aplica `upgrade head` a cada base. Si una falla, continúa con las demás
y reporta al final (no aborta todo). Corre SOLO en el pre-deploy de Railway (railway.api.toml,
`preDeployCommand`); ya no se corre a mano.

Endurecido para NO fallar en silencio:
- Banner estructurado al inicio y al final (nº de empresas + slugs migrados).
- 0 empresas → ERROR y exit != 0 (en prod siempre hay ≥1 tenant: un join/filtro roto o el control DB
  equivocado NO debe pasar como deploy verde). `--allow-empty` lo permite (primer deploy sin tenants).
- Exit 1 si alguna empresa falla.

Logging estructurado con slug/tenant (regla #6, nunca print; sin secretos en los logs).
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

# Códigos de salida: 0 = ok · 1 = alguna empresa falló · 2 = cero empresas (sin --allow-empty).
_EXIT_OK = 0
_EXIT_FALLIDAS = 1
_EXIT_VACIO = 2


def _empresas(control_url: str) -> list[dict]:
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as conn:
        return conn.execute(
            """SELECT e.id, e.slug, e.estado, t.connection_url_cifrada
               FROM empresas e JOIN tenant_databases t ON t.empresa_id = e.id
               WHERE e.estado IN ('activa', 'suspendida')
               ORDER BY e.id"""
        ).fetchall()


def migrate_all(empresas: list[dict], master_key: str) -> tuple[list[str], dict[str, str]]:
    """Migra cada empresa de la lista. Devuelve (ok_slugs, {slug: error}). No lanza si una falla."""
    ok: list[str] = []
    fallidas: dict[str, str] = {}
    for emp in empresas:
        slug = emp["slug"]
        try:
            url = decrypt(emp["connection_url_cifrada"], master_key)
            upgrade_tenant(url)
            ok.append(slug)
            log.info("tenant_migrado", slug=slug)
        except Exception as exc:  # noqa: BLE001 - se reporta y se continúa con las demás
            fallidas[slug] = str(exc)
            log.error("tenant_migracion_fallida", slug=slug, error=str(exc))
    return ok, fallidas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrar todas las empresas (tenancy.md §7).")
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="No fallar si hay 0 empresas (solo el primer deploy, antes de dar de alta tenants).",
    )
    args = parser.parse_args(argv)
    configure_logging()

    settings = get_settings()
    empresas = _empresas(settings.control_database_url)
    slugs = [e["slug"] for e in empresas]
    log.info("migrate_tenants_inicio", empresas=len(empresas), slugs=slugs)

    if not empresas:
        if args.allow_empty:
            log.warning(
                "migrate_tenants_sin_empresas",
                detalle="0 empresas y --allow-empty: se permite (primer deploy sin tenants).",
            )
            return _EXIT_OK
        log.error(
            "migrate_tenants_sin_empresas",
            detalle="0 empresas activas/suspendidas: en prod debe haber ≥1 tenant. Sospecha un "
                    "join/filtro roto o el control DB equivocado. Usa --allow-empty SOLO en el "
                    "primer deploy sin tenants.",
        )
        return _EXIT_VACIO

    ok, fallidas = migrate_all(empresas, settings.secrets_master_key)
    if fallidas:
        log.error(
            "migrate_tenants_fin", ok=len(ok), fallidas=len(fallidas),
            slugs_ok=ok, slugs_fallidas=list(fallidas.keys()),
            resumen=f"migrate_tenants: {len(ok)} OK, {len(fallidas)} FALLIDAS → {list(fallidas)}",
        )
        return _EXIT_FALLIDAS
    log.info(
        "migrate_tenants_fin", empresas=len(ok), slugs=ok,
        resumen=f"migrate_tenants: {len(ok)} empresa(s) → [{', '.join(ok)}] OK",
    )
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
