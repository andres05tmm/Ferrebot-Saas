"""Guard de apertura de caja (toggle `caja_obligatoria`) — invariante crítico de caja (TDD-primero).

Dos planos, espejo de test_ventas_control_stock: (1) HTTP por `POST /ventas` contra base efímera con
la dependencia `get_caja_obligatoria` overrideada (default OFF → 201 sin caja; ON → 409 `caja_no_abierta`
hasta que haya UNA caja abierta en la empresa, sin importar quién la abrió); (2) el reader
`cargar_caja_obligatoria` contra un control DB efímero (default False; lee 'true').

El 409 NO consume la Idempotency-Key: tras abrir caja, la MISMA key registra la venta una sola vez.
"""
import uuid

import httpx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config import get_settings
from core.db.session import get_tenant_db
from core.db.urls import tenant_url, to_async
from modules.caja.config import cargar_caja_obligatoria, get_caja_obligatoria
from modules.ventas.router import get_control_stock_estricto, router as ventas_router
from tests.conftest import create_database, drop_database


# ---- HTTP: POST /ventas con el guard ---------------------------------------
def _app(tenant, *, user_id: int, obligatoria: bool, capacidades: frozenset = frozenset({"pos"})) -> FastAPI:
    app = FastAPI()
    app.include_router(ventas_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol="vendedor")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: capacidades
    app.dependency_overrides[get_control_stock_estricto] = lambda: False
    app.dependency_overrides[get_caja_obligatoria] = lambda: obligatoria   # FAKE del toggle de empresa
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


def _venta_json(pid: int) -> dict:
    return {"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 1}]}


async def test_obligatoria_sin_caja_409_y_nada_registrado(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await seed_producto(s)

    app = _app(tenant, user_id=uid, obligatoria=True)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json=_venta_json(pid))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "caja_no_abierta"

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one() == 0


async def test_obligatoria_con_caja_de_otro_usuario_201(tenant, seed_producto):
    """Un cajón por empresa: la caja la abrió la empleada, pero el dueño puede vender (cualquier
    caja abierta de la EMPRESA habilita la venta, no la del usuario del request)."""
    from decimal import Decimal

    from modules.caja.repository import SqlCajaRepository
    from modules.caja.service import CajaService

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        otro = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Empleada','vendedor') RETURNING id"))
        ).scalar_one()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=otro, saldo_inicial=Decimal("50000"))
        await s.commit()

    app = _app(tenant, user_id=uid, obligatoria=True)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json=_venta_json(pid))
    assert r.status_code == 201, r.text


async def test_obligatoria_sin_capacidad_caja_201(tenant, seed_producto):
    """Toggle ON pero tenant SIN la feature `caja` (misconfiguración): la venta no se bloquea —
    default seguro, el guard exige la capacidad además del toggle."""
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await seed_producto(s)

    app = _app(tenant, user_id=uid, obligatoria=True, capacidades=frozenset({"ventas"}))
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json=_venta_json(pid))
    assert r.status_code == 201, r.text


async def test_default_off_sin_caja_201(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await seed_producto(s)

    app = _app(tenant, user_id=uid, obligatoria=False)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json=_venta_json(pid))
    assert r.status_code == 201, r.text


async def test_409_no_consume_idempotency_key(tenant, seed_producto):
    """Flujo del modal: el intento sin caja da 409; se abre caja; la MISMA key registra la venta
    (201) y su replay (200) deja UNA sola venta. El guard corre ANTES del servicio."""
    from decimal import Decimal

    from modules.caja.repository import SqlCajaRepository
    from modules.caja.service import CajaService

    async with AsyncSession(tenant.engine) as s:
        uid, pid = await seed_producto(s)

    key = f"venta-{uuid.uuid4().hex[:8]}"
    app = _app(tenant, user_id=uid, obligatoria=True)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/ventas", json=_venta_json(pid), headers={"Idempotency-Key": key})
        assert r1.status_code == 409, r1.text

        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("10000"))
            await s.commit()

        r2 = await c.post("/api/v1/ventas", json=_venta_json(pid), headers={"Idempotency-Key": key})
        assert r2.status_code == 201, r2.text
        r3 = await c.post("/api/v1/ventas", json=_venta_json(pid), headers={"Idempotency-Key": key})
        assert r3.status_code == 200, r3.text   # replay idempotente

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1


# ---- Bot: el handler `registrar_venta` respeta el guard (paridad con el API) ----
async def test_bot_registrar_venta_respeta_guard(tenant, seed_producto):
    """La venta por Telegram/voz NO pasa por el router HTTP (llama el servicio vía ai.tools): el
    guard vive también ahí. Sin caja → ErrorTool `caja_no_abierta` (recuperable, sin efectos);
    con caja abierta (de cualquier usuario) → la venta se registra."""
    from decimal import Decimal

    from ai.envelope import Contexto
    from ai.tools import Deps, ItemVentaArg, RegistrarVentaArgs, _registrar_venta
    from modules.caja.repository import SqlCajaRepository
    from modules.caja.service import CajaService
    from modules.clientes.repository import SqlClientesRepository
    from modules.clientes.service import ClientesService
    from modules.fiados.repository import SqlFiadosRepository
    from modules.fiados.service import FiadosService
    from modules.ventas.repository import SqlVentasRepository
    from modules.ventas.service import VentaService

    async def _toggle_on(_empresa_id: int) -> bool:
        return True

    def _deps(s):
        return Deps(
            ventas=VentaService(SqlVentasRepository(s)),
            caja=CajaService(SqlCajaRepository(s)),
            fiados=FiadosService(SqlFiadosRepository(s)),
            clientes=ClientesService(SqlClientesRepository(s)),
            caja_obligatoria=_toggle_on,
        )

    args = None
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        args = RegistrarVentaArgs(
            items=[ItemVentaArg(producto_id=pid, cantidad=Decimal("1"))], metodo_pago="efectivo"
        )
        ctx = Contexto(
            tenant_id=1, usuario_id=uid, rol="vendedor", origen="bot",
            capacidades=frozenset({"pos"}),
        )
        res = await _registrar_venta(args, ctx, _deps(s))
        assert res.ok is False and res.error == "caja_no_abierta" and res.recuperable is True
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 0

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        otro = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Empleada','vendedor') RETURNING id"))
        ).scalar_one()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=otro, saldo_inicial=Decimal("10000"))
        await s.commit()
        ctx = Contexto(
            tenant_id=1, usuario_id=uid, rol="vendedor", origen="bot",
            capacidades=frozenset({"pos"}),
        )
        res = await _registrar_venta(args, ctx, _deps(s))
        assert res.ok is True   # la caja de la EMPRESA habilita la venta del bot
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1


# ---- Reader: cargar_caja_obligatoria (control DB) ---------------------------
async def test_cargar_caja_obligatoria_default_y_true(monkeypatch):
    name = f"test_caja_oblig_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0})
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        async with AsyncSession(engine) as s:
            eid = (
                await s.execute(
                    text("INSERT INTO empresas (nombre, nit, slug, estado) VALUES ('PR','900','pr','activa') RETURNING id")
                )
            ).scalar_one()
            await s.commit()

            # Ausente → default OFF (no bloquear la venta).
            assert await cargar_caja_obligatoria(s, eid) is False

            await s.execute(
                text("INSERT INTO config_empresa (empresa_id, clave, valor) VALUES (:e,'caja_obligatoria','true')"),
                {"e": eid},
            )
            await s.commit()
            assert await cargar_caja_obligatoria(s, eid) is True
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
