"""Repositorio de identidades (core/tenancy/identidades_repo, ADR 0009). Requiere Postgres.

Control DB efímero migrado a head; verifica upsert idempotente por email, lookup case-insensitive,
normalización a minúsculas y set_password_hash. AsyncSession contra la base de control (patrón conftest).
"""
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.db.urls import tenant_url, to_async
from core.tenancy.identidades_repo import buscar_por_email, set_password_hash, upsert
from tests.conftest import create_database, drop_database


async def test_upsert_lookup_case_insensitive_y_set_password(monkeypatch):
    name = f"test_control_ident_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()

    create_database(name)
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        async with AsyncSession(engine) as s:
            empresa_id = (await s.execute(text(
                "INSERT INTO empresas (nombre, nit, slug, estado) "
                "VALUES ('Clínica','NIT-c','clinica','activa') RETURNING id"
            ))).scalar_one()
            await s.commit()

            # Alta: email con mayúsculas → se guarda normalizado; sin contraseña aún.
            ident = await upsert(s, email="Admin@Clinica.CO", empresa_id=empresa_id, usuario_id=7, rol="admin")
            await s.commit()
            assert ident.email == "admin@clinica.co"
            assert ident.password_hash is None and ident.activo is True
            assert ident.usuario_id == 7 and ident.rol == "admin"

            # Lookup case-insensitive (otro casing → misma identidad).
            encontrada = await buscar_por_email(s, "ADMIN@clinica.CO")
            assert encontrada is not None and encontrada.id == ident.id

            # Upsert idempotente por email: reapunta usuario/rol, NO duplica fila.
            de_nuevo = await upsert(s, email="admin@CLINICA.co", empresa_id=empresa_id, usuario_id=9, rol="vendedor")
            await s.commit()
            assert de_nuevo.id == ident.id and de_nuevo.usuario_id == 9 and de_nuevo.rol == "vendedor"
            n = (await s.execute(text("SELECT count(*) FROM identidades"))).scalar_one()
            assert n == 1

            # set_password_hash: queda persistido y recuperable.
            await set_password_hash(s, ident.id, "$argon2id$fake")
            await s.commit()
            assert (await buscar_por_email(s, "admin@clinica.co")).password_hash == "$argon2id$fake"

            # Email inexistente → None.
            assert await buscar_por_email(s, "nadie@otra.co") is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
