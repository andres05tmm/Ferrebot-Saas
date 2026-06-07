"""Integración: `resolve_tenant_by_wa_number` contra un control DB efímero (migrado a head).

Patrón de control DB efímero tomado de tests/test_bot_repos_control.py. Verifica el mapeo
phone_number_id → empresa, el número no mapeado (None) y que los mapeos inactivos no resuelven.
"""
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.crypto import encrypt
from core.db.urls import tenant_url, to_async
from core.tenancy.control_repo import resolve_tenant_by_wa_number
from tests.conftest import create_database, drop_database


@pytest.fixture
async def control_engine(monkeypatch):
    """Control DB efímero, migrado a head; lo destruye al final."""
    name = f"test_control_wa_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        yield engine
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)


async def _seed_empresa(s: AsyncSession, *, slug: str, estado: str = "activa") -> int:
    eid = (
        await s.execute(
            text(
                "INSERT INTO empresas (nombre, nit, slug, estado) "
                "VALUES (:n, :nit, :slug, :estado) RETURNING id"
            ),
            {"n": "Clinica", "nit": uuid.uuid4().hex[:9], "slug": slug, "estado": estado},
        )
    ).scalar_one()
    blob = encrypt("postgresql://u:p@h:5432/ferrebot_x", get_settings().secrets_master_key)
    await s.execute(
        text(
            "INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada) "
            "VALUES (:e, 'ferrebot_x', 'h', :blob)"
        ),
        {"e": eid, "blob": blob},
    )
    return eid


async def _map_wa(s: AsyncSession, *, pnid: str, empresa_id: int, estado: str = "activo") -> None:
    await s.execute(
        text(
            "INSERT INTO wa_numeros (phone_number_id, empresa_id, estado) "
            "VALUES (:p, :e, :estado)"
        ),
        {"p": pnid, "e": empresa_id, "estado": estado},
    )


async def test_resuelve_tenant_por_phone_number_id(control_engine):
    async with AsyncSession(control_engine) as s:
        eid = await _seed_empresa(s, slug="clinica1")
        await _map_wa(s, pnid="111222333", empresa_id=eid)
        await s.commit()

        tenant = await resolve_tenant_by_wa_number(s, "111222333")
        assert tenant is not None
        assert tenant.id == eid and tenant.estado == "activa"
        assert tenant.connection_url.startswith("postgresql://")  # se descifró


async def test_numero_no_mapeado_devuelve_none(control_engine):
    async with AsyncSession(control_engine) as s:
        eid = await _seed_empresa(s, slug="clinica2")
        await _map_wa(s, pnid="111", empresa_id=eid)
        await s.commit()
        assert await resolve_tenant_by_wa_number(s, "999-no-existe") is None


async def test_mapeo_inactivo_no_resuelve(control_engine):
    async with AsyncSession(control_engine) as s:
        eid = await _seed_empresa(s, slug="clinica3")
        await _map_wa(s, pnid="222", empresa_id=eid, estado="inactivo")
        await s.commit()
        assert await resolve_tenant_by_wa_number(s, "222") is None
