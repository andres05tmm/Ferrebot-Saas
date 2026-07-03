"""Helper para correr el árbol Alembic de tenant sobre una base concreta (programático).

La URL destino viaja por el objeto Config (aislado por llamada), NO por os.environ: dos
provisionings concurrentes (apps/worker/jobs.py corre en hilos) no se pisan la base destino.
"""
from alembic import command
from alembic.config import Config


def _run(tenant_url: str, action, revision: str) -> None:
    cfg = Config("migrations/tenant/alembic.ini")
    # configparser interpola '%': se escapa para passwords con caracteres percent-encoded.
    cfg.set_main_option("sqlalchemy.url", tenant_url.replace("%", "%%"))
    action(cfg, revision)


def upgrade_tenant(tenant_url: str, revision: str = "head") -> None:
    """Aplica las migraciones de negocio a `tenant_url` (driver normalizado en env.py)."""
    _run(tenant_url, command.upgrade, revision)


def downgrade_tenant(tenant_url: str, revision: str = "base") -> None:
    """Revierte las migraciones de negocio de `tenant_url` (para pruebas de migración)."""
    _run(tenant_url, command.downgrade, revision)
