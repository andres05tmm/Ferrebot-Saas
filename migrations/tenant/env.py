"""Alembic env del esquema de NEGOCIO. La URL destino llega por parámetro (tenancy.md §7).

Orden: -x db_url=... > env ALEMBIC_TENANT_URL. Se normaliza a driver sync (psycopg).
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.db.urls import to_sync

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # migraciones a mano (materializan schema.md)


def _url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    raw = x_args.get("db_url") or os.environ.get("ALEMBIC_TENANT_URL")
    if not raw:
        raise RuntimeError("Falta la URL del tenant: usa -x db_url=... o ALEMBIC_TENANT_URL")
    return to_sync(raw)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
