"""Router del pack pedidos (kanban del dashboard) por HTTP contra base efímera real.

Patrón test_cobranza_router: app mínima + ASGITransport + overrides. Cubre: gating por flag (404
sin `pack_pedidos`), RBAC (staff opera el kanban, config/zonas son de admin), avanzar estado por el
ciclo (409 en transición inválida) y el CRUD de zonas.
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.pedidos.router import router as pedidos_router

FLAG = frozenset({"pack_pedidos"})


def _app(tenant, *, rol: str = "admin", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(pedidos_router, prefix="/api/v1")

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


async def _seed_pedido_confirmado(tenant) -> int:
    async with AsyncSession(tenant.engine) as s:
        pedido_id = (
            await s.execute(
                text(
                    "INSERT INTO pedidos (cliente_telefono, estado, subtotal, total, direccion) "
                    "VALUES ('3001112233', 'confirmado', 36000, 39000, 'Cra 1 # 2-3') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO pedido_items (pedido_id, nombre, cantidad, precio_unitario, subtotal) "
                "VALUES (:p, 'Hamburguesa', 2, 18000, 36000)"
            ),
            {"p": pedido_id},
        )
        await s.commit()
    return pedido_id


async def _seed_cobro(tenant, pedido_id: int, *, estado: str, monto: str = "39000") -> None:
    async with AsyncSession(tenant.engine) as s:
        await s.execute(
            text(
                "INSERT INTO cobros (referencia, origen, origen_id, monto, estado, proveedor) "
                "VALUES (:ref, 'pedido', :oid, :m, :estado, 'manual')"
            ),
            {"ref": f"cob-{pedido_id}-{estado}", "oid": pedido_id, "m": monto, "estado": estado},
        )
        await s.commit()


async def test_sin_flag_pack_pedidos_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/pedidos")).status_code == 404


async def test_kanban_staff_lista_y_avanza(tenant):
    pedido_id = await _seed_pedido_confirmado(tenant)
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        lista = await c.get("/api/v1/pedidos?estado=confirmado")
        assert lista.status_code == 200
        assert [p["id"] for p in lista.json()] == [pedido_id]
        assert lista.json()[0]["items"][0]["nombre"] == "Hamburguesa"

        ok = await c.put(f"/api/v1/pedidos/{pedido_id}/estado", json={"estado": "en_preparacion"})
        assert ok.status_code == 200 and ok.json()["estado"] == "en_preparacion"

        # Saltarse el ciclo → 409; pedido inexistente → 404.
        assert (
            await c.put(f"/api/v1/pedidos/{pedido_id}/estado", json={"estado": "confirmado"})
        ).status_code == 409
        assert (
            await c.put("/api/v1/pedidos/99999/estado", json={"estado": "cancelado"})
        ).status_code == 404


async def test_listado_marca_pagado_segun_el_cobro(tenant):
    # Tres pedidos confirmados: uno con cobro pagado, uno con cobro pendiente, uno sin cobro.
    pagado_id = await _seed_pedido_confirmado(tenant)
    await _seed_cobro(tenant, pagado_id, estado="pagado")
    pendiente_id = await _seed_pedido_confirmado(tenant)
    await _seed_cobro(tenant, pendiente_id, estado="pendiente")
    sin_cobro_id = await _seed_pedido_confirmado(tenant)

    async with _cliente(_app(tenant, rol="vendedor")) as c:
        r = await c.get("/api/v1/pedidos?estado=confirmado")
        assert r.status_code == 200
        por_id = {p["id"]: p for p in r.json()}
        assert por_id[pagado_id]["pagado"] is True          # cobro pagado → insignia
        assert por_id[pendiente_id]["pagado"] is False       # cobro pendiente no cuenta
        assert por_id[sin_cobro_id]["pagado"] is False       # sin cobro, sin insignia


async def test_config_y_zonas_son_de_admin(tenant):
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        assert (await c.get("/api/v1/pedidos/config")).status_code == 403
        assert (
            await c.post("/api/v1/pedidos/zonas", json={"nombre": "Manga", "tarifa": "5000"})
        ).status_code == 403
        assert (await c.get("/api/v1/pedidos/zonas")).status_code == 200   # staff sí las LEE

    async with _cliente(_app(tenant, rol="admin")) as c:
        defaults = await c.get("/api/v1/pedidos/config")
        assert defaults.status_code == 200 and defaults.json()["tiempo_estimado_min"] == 45

        zona = await c.post("/api/v1/pedidos/zonas", json={"nombre": "Manga", "tarifa": "5000"})
        assert zona.status_code == 201
        zona_id = zona.json()["id"]
        assert (await c.delete(f"/api/v1/pedidos/zonas/{zona_id}")).status_code == 204
        assert (await c.get("/api/v1/pedidos/zonas")).json() == []   # desactivada (soft)
