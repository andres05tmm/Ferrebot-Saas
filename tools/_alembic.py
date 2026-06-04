"""Helper para correr el árbol Alembic de tenant sobre una base concreta (programático)."""
import os

from alembic import command
from alembic.config import Config


def _run(tenant_url: str, action, revision: str) -> None:
    cfg = Config("migrations/tenant/alembic.ini")
    previo = os.environ.get("ALEMBIC_TENANT_URL")
    os.environ["ALEMBIC_TENANT_URL"] = tenant_url
    try:
        action(cfg, revision)
    finally:
        if previo is None:
            os.environ.pop("ALEMBIC_TENANT_URL", None)
        else:
            os.environ["ALEMBIC_TENANT_URL"] = previo


def upgrade_tenant(tenant_url: str, revision: str = "head") -> None:
    """Aplica las migraciones de negocio a `tenant_url` (driver normalizado en env.py)."""
    _run(tenant_url, command.upgrade, revision)


def downgrade_tenant(tenant_url: str, revision: str = "base") -> None:
    """Revierte las migraciones de negocio de `tenant_url` (para pruebas de migración)."""
    _run(tenant_url, command.downgrade, revision)
