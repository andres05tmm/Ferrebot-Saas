"""Alembic env del CONTROL DB. URL desde CONTROL_DATABASE_URL (driver sync psycopg)."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.config import get_settings
from core.db.urls import to_sync

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # migraciones escritas a mano (materializan schema.md)


def _url() -> str:
    return to_sync(get_settings().control_database_url)


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
