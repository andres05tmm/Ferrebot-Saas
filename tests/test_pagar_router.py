"""Router del pack pagar (página Cuentas por pagar del dashboard) por HTTP contra base efímera real.

Patrón test_cobranza_router: app mínima + ASGITransport + overrides de auth, sesión del tenant
(commit) y capacidades. Cubre: gating por flag (404 sin `pack_pagar`), RBAC (todo es de admin: las
cuentas por pagar son dato sensible), config get-or-create con defaults, la lista clasificada
(vencidas / por vencer) y el AISLAMIENTO multi-tenant (la empresa A nunca lista cuentas de B).
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from core.db.session import get_tenant_db
from datetime import timedelta
from modules.pagar.router import router as pagar_router

FLAG = frozenset({"pack_pagar"})


def _app(tenant, *, rol: str = "admin", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(pagar_router, prefix="/api/v1")

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


async def _seed_factura(tenant, *, factura_id="F-001", proveedor="Tornillos SA",
                        pendiente="100000", fecha_vencimiento=None) -> str:
    async with AsyncSession(tenant.engine) as s:
        await s.execute(
            text(
                "INSERT INTO facturas_proveedores "
                "(id, proveedor, total, pagado, pendiente, estado, fecha, fecha_vencimiento) "
                "VALUES (:id, :p, :pend, 0, :pend, 'pendiente', :f, :fv)"
            ),
            {"id": factura_id, "p": proveedor, "pend": pendiente,
             "f": today_co(), "fv": fecha_vencimiento},
        )
        await s.commit()
    return factura_id


async def test_sin_flag_pack_pagar_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())  # sin la capacidad
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/pagar/cuentas")).status_code == 404


async def test_rbac_vendedor_no_ve_cuentas(tenant):
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        assert (await c.get("/api/v1/pagar/cuentas")).status_code == 403
        assert (await c.get("/api/v1/pagar/config")).status_code == 403


async def test_config_defaults_y_actualizacion(tenant):
    async with _cliente(_app(tenant)) as c:
        defaults = await c.get("/api/v1/pagar/config")
        assert defaults.status_code == 200
        assert defaults.json()["cadencia_dias"] == 3 and defaults.json()["dias_aviso_previo"] == 3

        upd = await c.put(
            "/api/v1/pagar/config",
            json={"activo": True, "dias_aviso_previo": 5, "cadencia_dias": 7,
                  "hora_inicio": "08:00", "hora_fin": "18:00", "plazo_default_dias": 45},
        )
        assert upd.status_code == 200
        assert upd.json()["dias_aviso_previo"] == 5 and upd.json()["plazo_default_dias"] == 45


async def test_cuentas_clasificadas(tenant):
    await _seed_factura(tenant, factura_id="VENCIDA", fecha_vencimiento=today_co() - timedelta(days=2))
    await _seed_factura(tenant, factura_id="PORVENCER", fecha_vencimiento=today_co() + timedelta(days=2))
    await _seed_factura(tenant, factura_id="LEJANA", fecha_vencimiento=today_co() + timedelta(days=40))
    async with _cliente(_app(tenant)) as c:
        r = await c.get("/api/v1/pagar/cuentas")
        assert r.status_code == 200
        por_id = {x["factura_id"]: x for x in r.json()}
        assert por_id["VENCIDA"]["vencida"] and not por_id["VENCIDA"]["por_vencer"]
        assert por_id["PORVENCER"]["por_vencer"] and not por_id["PORVENCER"]["vencida"]
        # la lejana aparece en la lista (tiene saldo) pero ni vencida ni por vencer
        assert not por_id["LEJANA"]["vencida"] and not por_id["LEJANA"]["por_vencer"]


async def test_aislamiento_router_a_no_lista_cuentas_de_b(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    await _seed_factura(empresa_a, factura_id="A-1", fecha_vencimiento=today_co())

    async with _cliente(_app(empresa_b)) as c:
        assert (await c.get("/api/v1/pagar/cuentas")).json() == []   # B no ve nada de A
    async with _cliente(_app(empresa_a)) as c:
        assert [x["factura_id"] for x in (await c.get("/api/v1/pagar/cuentas")).json()] == ["A-1"]
