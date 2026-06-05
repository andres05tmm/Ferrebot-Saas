"""Aprovisionar una empresa (tenancy.md §8). Idempotente: se puede reintentar.

CREATE DATABASE -> registrar en control (URL cifrada) -> migrar -> sembrar -> admin -> activa.
Usa driver sync (psycopg); no toca el caché de engines async de la API.
"""
import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.crypto import encrypt
from core.db.urls import tenant_url, to_libpq
from tools._alembic import upgrade_tenant


def _db_name(slug: str) -> str:
    return f"ferrebot_{slug}"


def _create_database(admin_url: str, db_name: str) -> None:
    with psycopg.connect(to_libpq(admin_url), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')


def _register_control(control_url: str, *, slug, nombre, nit, db_name, host, conn_cifrada) -> int:
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id FROM empresas WHERE slug = %s", (slug,)).fetchone()
        if row:
            empresa_id = row["id"]
            conn.execute("UPDATE empresas SET nombre=%s, nit=%s WHERE id=%s", (nombre, nit, empresa_id))
        else:
            empresa_id = conn.execute(
                "INSERT INTO empresas (nombre, nit, slug, estado) VALUES (%s,%s,%s,'provisionando') RETURNING id",
                (nombre, nit, slug),
            ).fetchone()["id"]
        conn.execute(
            """INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (empresa_id) DO UPDATE
               SET db_name=EXCLUDED.db_name, host=EXCLUDED.host,
                   connection_url_cifrada=EXCLUDED.connection_url_cifrada""",
            (empresa_id, db_name, host, conn_cifrada),
        )
        conn.commit()
        return empresa_id


def _seed(tenant_url_: str, admin_nombre: str) -> None:
    with psycopg.connect(to_libpq(tenant_url_)) as conn:
        existe = conn.execute("SELECT 1 FROM usuarios WHERE rol='admin' LIMIT 1").fetchone()
        if not existe:
            conn.execute("INSERT INTO usuarios (nombre, rol) VALUES (%s,'admin')", (admin_nombre,))
        # La config no-secreta por empresa vive en el CONTROL DB (config_empresa con empresa_id),
        # no en la app DB (tabla retirada en tenant 0005). No se siembra nada aquí.
        conn.commit()


def _activar(control_url: str, empresa_id: int) -> None:
    with psycopg.connect(to_libpq(control_url), autocommit=True) as conn:
        conn.execute("UPDATE empresas SET estado='activa' WHERE id=%s", (empresa_id,))


def provision_tenant(slug: str, nombre: str, nit: str, admin_nombre: str = "Admin") -> int:
    """Aprovisiona la empresa y devuelve su empresa_id. Idempotente."""
    settings = get_settings()
    db_name = _db_name(slug)
    host = settings.tenants_direct_url_base
    conn_url = tenant_url(settings.tenants_direct_url_base, db_name)

    _create_database(settings.admin_database_url, db_name)
    cifrada = encrypt(conn_url, settings.secrets_master_key)
    empresa_id = _register_control(
        settings.control_database_url, slug=slug, nombre=nombre, nit=nit,
        db_name=db_name, host=host, conn_cifrada=cifrada,
    )
    upgrade_tenant(conn_url)
    _seed(conn_url, admin_nombre)
    _activar(settings.control_database_url, empresa_id)
    return empresa_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aprovisionar una empresa (tenant).")
    parser.add_argument("slug")
    parser.add_argument("nombre")
    parser.add_argument("nit")
    parser.add_argument("--admin", default="Admin", help="Nombre del usuario admin inicial")
    args = parser.parse_args(argv)
    empresa_id = provision_tenant(args.slug, args.nombre, args.nit, admin_nombre=args.admin)
    print(f"empresa '{args.slug}' aprovisionada (id={empresa_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
