"""Router REST del pack reservas (dashboard/recepción) contra base efímera real.

Patrón test_pagar_router: app mínima + ASGITransport + overrides de auth, sesión del tenant (commit)
y capacidades. Cubre el gating por flag (404 sin `pack_reservas`), listar habitaciones libres, crear
la reserva, y —invariantes críticos, test-primero— la IDEMPOTENCIA de la reserva (misma Idempotency-Key
→ 200 replay, misma cita, sin duplicar) y el AISLAMIENTO multi-tenant (una reserva/recurso de A jamás
es alcanzable desde B).
"""
from datetime import timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from core.db.session import get_tenant_db
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import AgendaConfigCrear, RecursoCrear, ServicioCrear
from modules.reservas.router import router as reservas_router
# `citas.venta_id` es FK a `ventas`: registrar el modelo Venta en el mapper para que el flush de la
# cita (reserva) resuelva la FK, igual que en producción (main.py incluye el router de ventas).
import modules.ventas.models  # noqa: F401,E402

FLAG = frozenset({"pack_reservas"})


def _app(tenant, *, rol: str = "vendedor", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(reservas_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: capacidades
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def _seed_habitacion(tenant, *, nombre="Hab 101", precio="100000") -> int:
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlAgendaRepository(s)
        servicio = await repo.crear_servicio(
            ServicioCrear(nombre="Noche", duracion_min=60, precio=Decimal(precio))
        )
        recurso = await repo.crear_recurso(RecursoCrear(nombre=nombre, tipo="habitacion"))
        await repo.asignar_servicio(recurso_id=recurso.id, servicio_id=servicio.id)
        await repo.guardar_config(AgendaConfigCrear())
        await s.commit()
        return recurso.id


def _checkin() -> str:
    return (today_co() + timedelta(days=3)).isoformat()


async def test_sin_flag_pack_reservas_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())
    async with _cliente(app) as c:
        r = await c.get(f"/api/v1/reservas/habitaciones?checkin={_checkin()}&noches=2")
        assert r.status_code == 404


async def test_habitaciones_libres_lista_con_precio_y_total(tenant):
    await _seed_habitacion(tenant, precio="100000")
    async with _cliente(_app(tenant)) as c:
        r = await c.get(f"/api/v1/reservas/habitaciones?checkin={_checkin()}&noches=2")
        assert r.status_code == 200
        libres = r.json()
        assert len(libres) == 1
        assert libres[0]["nombre"] == "Hab 101"
        assert Decimal(libres[0]["precio_noche"]) == Decimal("100000")
        assert Decimal(libres[0]["total"]) == Decimal("200000")  # 2 noches


async def test_crear_reserva_ocupa_la_habitacion(tenant):
    recurso_id = await _seed_habitacion(tenant)
    checkin = _checkin()
    async with _cliente(_app(tenant)) as c:
        r = await c.post("/api/v1/reservas", json={
            "recurso_id": recurso_id, "checkin": checkin, "noches": 2,
            "cliente_nombre": "Ana", "cliente_telefono": "3001112233",
        })
        assert r.status_code == 201, r.text
        assert r.json()["cita"]["recurso_id"] == recurso_id
        # ya reservada: para las mismas fechas no aparece libre
        libres = (await c.get(f"/api/v1/reservas/habitaciones?checkin={checkin}&noches=2")).json()
        assert libres == []
        # y un segundo intento sin idempotencia choca con el cupo
        r2 = await c.post("/api/v1/reservas", json={
            "recurso_id": recurso_id, "checkin": checkin, "noches": 2,
            "cliente_nombre": "Otro", "cliente_telefono": "3009998877",
        })
        assert r2.status_code == 409


async def test_reserva_idempotente_no_duplica(tenant):
    recurso_id = await _seed_habitacion(tenant)
    checkin = _checkin()
    body = {"recurso_id": recurso_id, "checkin": checkin, "noches": 2,
            "cliente_nombre": "Ana", "cliente_telefono": "3001112233"}
    headers = {"Idempotency-Key": "reserva-abc-123"}
    async with _cliente(_app(tenant)) as c:
        r1 = await c.post("/api/v1/reservas", json=body, headers=headers)
        assert r1.status_code == 201
        r2 = await c.post("/api/v1/reservas", json=body, headers=headers)
        assert r2.status_code == 200  # replay
        assert r2.json()["replay"] is True
        assert r2.json()["cita"]["id"] == r1.json()["cita"]["id"]  # misma cita, no otra


async def test_noches_fuera_de_rango_da_422(tenant):
    recurso_id = await _seed_habitacion(tenant)
    async with _cliente(_app(tenant)) as c:
        r = await c.post("/api/v1/reservas", json={
            "recurso_id": recurso_id, "checkin": _checkin(), "noches": 99,
            "cliente_nombre": "Ana", "cliente_telefono": "3001112233",
        })
        assert r.status_code == 422


async def test_aislamiento_recurso_de_a_no_es_reservable_desde_b(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    recurso_a = await _seed_habitacion(empresa_a)
    # B tiene su propia habitación (distinta base): la de A no existe en B.
    async with _cliente(_app(empresa_b)) as c:
        # habitaciones de B no incluyen la de A (base separada)
        assert (await c.get(f"/api/v1/reservas/habitaciones?checkin={_checkin()}&noches=1")).json() == []
        # reservar el recurso_id de A contra la base de B → 404 (no existe allí)
        r = await c.post("/api/v1/reservas", json={
            "recurso_id": recurso_a, "checkin": _checkin(), "noches": 1,
            "cliente_nombre": "X", "cliente_telefono": "300",
        })
        assert r.status_code == 404
