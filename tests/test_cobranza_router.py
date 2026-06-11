"""Router del pack cobranza (página Cartera del dashboard) por HTTP contra base efímera real.

Patrón test_faq_router: app mínima + ASGITransport + overrides de auth, sesión del tenant (commit) y
capacidades. Cubre: gating por flag (404 sin `pack_cobranza`), RBAC (todo es de admin: la cartera es
dato sensible), config get-or-create con defaults, deudores con promesa vigente, verificación de un
pago reportado y el opt-out desde el dashboard.
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.cobranza.router import router as cobranza_router

FLAG = frozenset({"pack_cobranza"})


def _app(tenant, *, rol: str = "admin", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(cobranza_router, prefix="/api/v1")

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


async def _seed_deudor(tenant, *, nombre="Ana", telefono="3001112233", saldo="150000") -> int:
    async with AsyncSession(tenant.engine) as s:
        cliente_id = (
            await s.execute(
                text(
                    "INSERT INTO clientes (nombre, telefono, saldo_fiado) "
                    "VALUES (:n, :t, :s) RETURNING id"
                ),
                {"n": nombre, "t": telefono, "s": saldo},
            )
        ).scalar_one()
        await s.commit()
    return cliente_id


async def test_sin_flag_pack_cobranza_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())  # sin la capacidad
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/cobranza/deudores")).status_code == 404


async def test_rbac_vendedor_no_ve_cartera(tenant):
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        assert (await c.get("/api/v1/cobranza/deudores")).status_code == 403
        assert (await c.get("/api/v1/cobranza/config")).status_code == 403


async def test_config_defaults_y_actualizacion(tenant):
    async with _cliente(_app(tenant)) as c:
        defaults = await c.get("/api/v1/cobranza/config")
        assert defaults.status_code == 200
        assert defaults.json()["cadencia_dias"] == 7 and defaults.json()["max_recordatorios"] == 3

        upd = await c.put(
            "/api/v1/cobranza/config",
            json={"cadencia_dias": 10, "max_recordatorios": 2, "hora_inicio": "08:00",
                  "hora_fin": "18:00", "saldo_minimo": "5000", "activo": True},
        )
        assert upd.status_code == 200 and upd.json()["cadencia_dias"] == 10


async def test_deudores_y_opt_out(tenant):
    cliente_id = await _seed_deudor(tenant)
    async with _cliente(_app(tenant)) as c:
        deudores = await c.get("/api/v1/cobranza/deudores")
        assert deudores.status_code == 200
        fila = next(d for d in deudores.json() if d["cliente_id"] == cliente_id)
        assert fila["nombre"] == "Ana" and not fila["opt_out"]

        r = await c.put(f"/api/v1/cobranza/clientes/{cliente_id}/opt-out", json={"opt_out": True})
        assert r.status_code == 204

        fila = next(
            d for d in (await c.get("/api/v1/cobranza/deudores")).json()
            if d["cliente_id"] == cliente_id
        )
        assert fila["opt_out"] is True


async def test_recuperado_endpoint(tenant):
    async with _cliente(_app(tenant)) as c:
        r = await c.get("/api/v1/cobranza/recuperado?dias=30")
        assert r.status_code == 200
        assert r.json() == {"total": "0", "dias": 30}

        assert (await c.get("/api/v1/cobranza/recuperado?dias=0")).status_code == 422


async def test_verificar_pago_reportado(tenant):
    cliente_id = await _seed_deudor(tenant)
    async with AsyncSession(tenant.engine) as s:
        pago_id = (
            await s.execute(
                text(
                    "INSERT INTO pagos_reportados (cliente_id, telefono, nota) "
                    "VALUES (:c, '3001112233', 'Nequi') RETURNING id"
                ),
                {"c": cliente_id},
            )
        ).scalar_one()
        await s.commit()

    async with _cliente(_app(tenant)) as c:
        pendientes = await c.get("/api/v1/cobranza/pagos-reportados")
        assert [p["id"] for p in pendientes.json()] == [pago_id]

        ok = await c.post(f"/api/v1/cobranza/pagos-reportados/{pago_id}/verificar")
        assert ok.status_code == 200 and ok.json()["verificado"] is True
        assert (await c.get("/api/v1/cobranza/pagos-reportados")).json() == []

        assert (await c.post("/api/v1/cobranza/pagos-reportados/99999/verificar")).status_code == 404
