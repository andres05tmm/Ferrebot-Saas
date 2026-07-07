"""Regresión (integración D3, Fase 3): las mutaciones de obra/máquina deben SERIALIZAR su respuesta.

`Obra` y `Maquina` mapean `actualizado_en` con `onupdate=func.now()`. Tras un UPDATE ese atributo queda
EXPIRADO (lo computa el servidor); si el router lo serializa (`ObraLeer`/`MaquinaLeer.model_validate`) sobre
una sesión ASYNC sin repoblarlo, el acceso perezoso dispara IO fuera del contexto greenlet → `MissingGreenlet`
(500). Los tests de router de Fase 1 usan un repo FAKE (obra con `actualizado_en` literal), así que nunca
tocaban ese camino; este test corre el repo/servicio REALES sobre una base efímera y ejerce la
serialización HTTP exacta. El fix (repos: `refresh` tras el `flush`) lo mantiene verde.

No es TDD test-primero: no toca un invariante del carve-out (aislamiento/idempotencia/movimiento), sino
la correcta serialización de un endpoint ya existente. Cadencia código-primero (regla de desarrollo).
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.maquinaria.router import router as maquinaria_router
from modules.obra.router import router as obras_router

# Registrar los modelos del vertical en la metadata (side-effect) para que resuelvan sus tablas/FKs.
import modules.obra.models  # noqa: E402,F401
import modules.maquinaria.models  # noqa: E402,F401


def _app(tenant) -> FastAPI:
    app = FastAPI()
    app.include_router(obras_router, prefix="/api/v1")
    app.include_router(maquinaria_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol="admin")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"obras", "maquinaria"})
    return app


async def test_patch_obra_estado_y_metadata_serializan_tras_update(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('C', 0) RETURNING id"))
        ).scalar_one()
        await s.commit()

    transport = ASGITransport(app=_app(tenant), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/v1/obras", json={"cliente_id": cid, "nombre": "Obra X"})
        assert r.status_code == 201, r.text
        oid = r.json()["id"]

        # PATCH estado: serializa la obra RECIÉN actualizada (el camino que disparaba MissingGreenlet).
        r = await c.patch(f"/api/v1/obras/{oid}/estado", json={"estado": "EN_EJECUCION"})
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "EN_EJECUCION"
        assert r.json()["actualizado_en"]   # el campo onupdate viaja serializado, sin 500

        # PATCH metadatos: mismo camino de serialización tras UPDATE.
        r = await c.patch(f"/api/v1/obras/{oid}", json={"notas": "revisar acceso"})
        assert r.status_code == 200, r.text
        assert r.json()["notas"] == "revisar acceso"


async def test_patch_maquina_serializa_tras_update(tenant):
    transport = ASGITransport(app=_app(tenant), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/v1/maquinas",
            json={"codigo": "M-REG", "nombre": "Retro", "tipo": "excavadora", "precio_hora_default": "100000"},
        )
        assert r.status_code == 201, r.text
        mid = r.json()["id"]

        r = await c.patch(f"/api/v1/maquinas/{mid}", json={"notas": "mantenimiento al día"})
        assert r.status_code == 200, r.text
        assert r.json()["notas"] == "mantenimiento al día"
        assert r.json()["actualizado_en"]
