"""Router de conciliación bancaria por HTTP contra base efímera real (patrón test_pagar_router).

Cubre: gating por flag (404 sin `conciliacion_bancaria`), RBAC (todo es de admin), ingesta idempotente
por HTTP, el ciclo sugerir→conciliar de un match único, y el 422 de un enlace inválido.
"""
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.bancos.router import router as bancos_router

FLAG = frozenset({"conciliacion_bancaria"})
_DIA = "2026-06-15"
_TS = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5)))


def _app(tenant, *, rol: str = "admin", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(bancos_router, prefix="/api/v1")

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


async def _venta(tenant, *, total: str, consecutivo: int) -> int:
    async with AsyncSession(tenant.engine) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        vid = (
            await s.execute(
                text(
                    "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                    "metodo_pago, estado) VALUES (:c,:uid,:f,:t,0,:t,'transferencia','completada') RETURNING id"
                ),
                {"c": consecutivo, "uid": uid, "f": _TS, "t": total},
            )
        ).scalar_one()
        await s.commit()
    return vid


def _linea(ref: str, monto: str, naturaleza="credito") -> dict:
    return {"referencia_bancaria": ref, "fecha": _DIA, "monto": monto, "naturaleza": naturaleza}


async def test_sin_flag_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/bancos/movimientos")).status_code == 404


async def test_rbac_vendedor_no_entra(tenant):
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        assert (await c.get("/api/v1/bancos/movimientos")).status_code == 403
        assert (await c.post("/api/v1/bancos/ingesta", json=[])).status_code == 403


async def test_ingesta_idempotente_por_http(tenant):
    async with _cliente(_app(tenant)) as c:
        r1 = await c.post("/api/v1/bancos/ingesta", json=[_linea("R1", "100000"), _linea("R2", "50000")])
        assert r1.status_code == 200 and r1.json() == {"insertados": 2, "duplicados": 0}
        r2 = await c.post("/api/v1/bancos/ingesta", json=[_linea("R1", "100000")])
        assert r2.json() == {"insertados": 0, "duplicados": 1}


async def test_ciclo_sugerir_y_conciliar(tenant):
    await _venta(tenant, total="250000", consecutivo=1)
    async with _cliente(_app(tenant)) as c:
        await c.post("/api/v1/bancos/ingesta", json=[_linea("RV", "250000")])
        assert (await c.post("/api/v1/bancos/sugerir")).json() == {"sugeridos": 1}

        movs = (await c.get("/api/v1/bancos/movimientos", params={"estado": "sugerido"})).json()
        assert len(movs) == 1
        mov_id = movs[0]["movimiento"]["id"]
        cand = movs[0]["candidatos"][0]
        assert cand["tipo"] == "venta"

        conf = await c.post(
            f"/api/v1/bancos/movimientos/{mov_id}/conciliar",
            json={"tipo": cand["tipo"], "id_interno": cand["id"]},
        )
        assert conf.status_code == 200
        assert conf.json()["estado_conciliacion"] == "conciliado"


async def test_conciliar_enlace_invalido_422(tenant):
    async with _cliente(_app(tenant)) as c:
        await c.post("/api/v1/bancos/ingesta", json=[_linea("RZ", "777")])
        mov_id = (await c.get("/api/v1/bancos/movimientos")).json()[0]["movimiento"]["id"]
        r = await c.post(
            f"/api/v1/bancos/movimientos/{mov_id}/conciliar",
            json={"tipo": "venta", "id_interno": 999999},
        )
        assert r.status_code == 422
