"""Derivación de URLs por driver.

Forma base en .env: postgresql://user:pass@host:port/db
- API (runtime async): postgresql+asyncpg://...
- Alembic / provisioning / tools (sync): postgresql+psycopg://...
- Listener SSE: conexión DIRECTA con asyncpg (no PgBouncer) — usa la async.
"""
import re

_SCHEME = re.compile(r"^postgres(ql)?(\+[a-z0-9]+)?://")


def _swap_scheme(url: str, driver: str) -> str:
    return _SCHEME.sub(f"postgresql+{driver}://", url, count=1)


def to_async(url: str) -> str:
    """URL para SQLAlchemy async (asyncpg)."""
    return _swap_scheme(url, "asyncpg")


def to_sync(url: str) -> str:
    """URL para SQLAlchemy/Alembic sync (psycopg v3)."""
    return _swap_scheme(url, "psycopg")


def to_libpq(url: str) -> str:
    """URL para drivers libpq directos (psycopg.connect, asyncpg.connect): sin '+driver'."""
    return _swap_scheme(url, "").replace("postgresql+://", "postgresql://", 1)


def tenant_url(base: str, db_name: str) -> str:
    """Compone la URL base de tenants con el nombre de la base de la empresa."""
    return f"{base.rstrip('/')}/{db_name}"
