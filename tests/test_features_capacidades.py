"""RC-1 — `get_capacidades` efectivas desde el control DB (integración, DB efímero).

Verifica que `get_capacidades` resuelve plan ∪ overrides para la empresa del request, que la feature
presente pasa el gate y que `verificar_feature` da 404 cuando un override la deshabilitó.
Patrón de control DB efímero de `test_llm_stores`; aquí se ruta `control_session()` al DB efímero.
"""
import uuid
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import text

import core.db.session as session_mod
from core.auth.features import get_capacidades, verificar_feature
from core.config import get_settings
from core.db.session import control_session
from core.db.urls import tenant_url
from tests.conftest import create_database, drop_database


async def test_get_capacidades_efectivas(monkeypatch):
    name = f"test_control_feat_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    # `control_session()` reconstruye su sessionmaker contra el DB efímero:
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        async with control_session() as s:
            pid = (
                await s.execute(
                    text("INSERT INTO planes (nombre, limites) VALUES ('Pro', CAST(:l AS JSONB)) RETURNING id"),
                    {"l": '{"features": ["facturacion_electronica", "ventas"]}'},
                )
            ).scalar_one()
            eid = (
                await s.execute(
                    text(
                        "INSERT INTO empresas (nombre, nit, slug, estado, plan_id) "
                        "VALUES ('Punto Rojo','900','pr','activa',:p) RETURNING id"
                    ),
                    {"p": pid},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO empresa_features (empresa_id, feature, habilitada) VALUES "
                    "(:e,'documento_soporte',true), (:e,'ventas',false)"
                ),
                {"e": eid},
            )

        request = SimpleNamespace(state=SimpleNamespace(tenant=SimpleNamespace(id=eid)))
        caps = await get_capacidades(request)
        # plan {facturacion_electronica, ventas}; override +documento_soporte, −ventas
        assert caps == frozenset({"facturacion_electronica", "documento_soporte"})
        verificar_feature("facturacion_electronica", caps)        # no lanza
        with pytest.raises(HTTPException) as exc:
            verificar_feature("ventas", caps)                     # override la deshabilitó
        assert exc.value.status_code == 404
    finally:
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
