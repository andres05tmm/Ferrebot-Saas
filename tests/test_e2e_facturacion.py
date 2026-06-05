"""RC-2 — smoke end-to-end de facturación (cierra E4). Requiere Postgres + Redis; sin red real a DIAN.

Valida el glue de runtime de punta a punta:
  1. POST /api/v1/facturas (API real: TenantMiddleware + feature gate + pool ARQ del lifespan) → crea
     el documento `pendiente` y encola `emitir_documento` en Redis.
  2. on_startup + emitir_documento (worker) corren el job contra el tenant, con MATIAS mockeado
     (httpx.MockTransport): login/cities/invoice canned.
  3. La factura del tenant pasa de `pendiente` a `aceptada` con el CUFE devuelto por el mock.
"""
import uuid

import httpx
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import core.db.session as session_mod
from apps.api.main import create_app, lifespan
from apps.worker.main import emitir_documento, on_startup
from core.auth import Principal, get_current_user
from core.config import get_settings
from core.crypto import encrypt, encrypt_split
from core.db.urls import tenant_url
from core.tenancy.cache import control_cache
from modules.facturacion.matias_client import MatiasClient
from tests.conftest import create_database, drop_database

_CUFE = "C" * 40


def _matias_handler(request: httpx.Request) -> httpx.Response:
    """MockTransport de MATIAS: login + cities + invoice canned (cero red real)."""
    path = request.url.path
    if path.endswith("/auth/login"):
        return httpx.Response(200, json={"token": "T", "expires_in": 3600})
    if path.endswith("/cities"):
        return httpx.Response(200, json={"data": [{"code": "5001", "id": "149"}]})
    if path.endswith("/invoice"):
        return httpx.Response(200, json={"success": True, "XmlDocumentKey": _CUFE})
    return httpx.Response(404, json={})


async def _seed_control(master: str, db_name: str, tenant_url_base: str) -> int:
    """Siembra empresa 'pr' activa + plan(feature) + secretos/config MATIAS + tenant_databases."""
    async with session_mod.control_session() as cs:
        pid = (await cs.execute(
            text("INSERT INTO planes (nombre, limites) VALUES ('Pro', CAST(:l AS JSONB)) RETURNING id"),
            {"l": '{"features": ["facturacion_electronica"]}'},
        )).scalar_one()
        eid = (await cs.execute(
            text("INSERT INTO empresas (nombre, nit, slug, estado, plan_id) "
                 "VALUES ('Punto Rojo','900','pr','activa',:p) RETURNING id"), {"p": pid},
        )).scalar_one()
        em_ct, em_n = encrypt_split("bot@empresa.co", master)
        pw_ct, pw_n = encrypt_split("secreto", master)
        await cs.execute(
            text("INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) VALUES "
                 "(:e,'matias_email',:e1,:n1), (:e,'matias_password',:e2,:n2)"),
            {"e": eid, "e1": em_ct, "n1": em_n, "e2": pw_ct, "n2": pw_n},
        )
        await cs.execute(
            text("INSERT INTO config_empresa (empresa_id, clave, valor) VALUES "
                 "(:e,'matias_base_url','http://matias.mock'),(:e,'matias_resolution','18760000001'),"
                 "(:e,'matias_prefix','FPR'),(:e,'matias_notes','Punto Rojo'),(:e,'matias_city_id','149')"),
            {"e": eid},
        )
        await cs.execute(
            text("INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada, region) "
                 "VALUES (:e,:d,'localhost',:u,'local')"),
            {"e": eid, "d": db_name, "u": encrypt(tenant_url_base, master)},
        )
    return eid


async def _seed_tenant(engine) -> int:
    """Siembra vendedor + producto + cliente NIT + venta + detalle; devuelve venta_id."""
    async with AsyncSession(engine) as s:
        uid = (await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))).scalar_one()
        pid = (await s.execute(
            text("INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                 "VALUES ('Martillo','unidad',11900,19,false,true) RETURNING id"))).scalar_one()
        cli = (await s.execute(
            text("INSERT INTO clientes (nombre, tipo_documento, documento, ciudad_dane, regimen, saldo_fiado) "
                 "VALUES ('Ferre SAS','NIT','900123456','5001','responsable_iva',0) RETURNING id"))).scalar_one()
        cons = (await s.execute(text("SELECT nextval('ventas_consecutivo_seq')"))).scalar_one()
        vid = (await s.execute(
            text("INSERT INTO ventas (consecutivo, cliente_id, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago) "
                 "VALUES (:c,:cli,:u, now(), 10000, 1900, 11900, 'efectivo') RETURNING id"),
            {"c": cons, "cli": cli, "u": uid})).scalar_one()
        await s.execute(
            text("INSERT INTO ventas_detalle (venta_id, producto_id, descripcion, cantidad, precio_unitario, iva) "
                 "VALUES (:v,:p,'martillo',1,11900,19)"), {"v": vid, "p": pid})
        await s.commit()
    return vid


async def test_e2e_pendiente_a_aceptada(tenant, monkeypatch):
    # --- control DB efímero: ruta control_session() al efímero ---
    control_name = f"test_control_e2e_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    control_cache.invalidate("pr")
    # --- MATIAS mockeado (cero red real), memoizado por instancia ---
    def _mock_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=httpx.MockTransport(_matias_handler), base_url=self._cred.base_url)
        return self._client
    monkeypatch.setattr(MatiasClient, "_get_client", _mock_get_client)

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        master = get_settings().secrets_master_key
        tenant_id = await _seed_control(master, tenant.name, tenant.url)
        venta_id = await _seed_tenant(tenant.engine)

        # 1) Encolar vía API real (con su lifespan: pool ARQ sobre Redis) ---
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="vendedor")
        async with lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post(
                    "/api/v1/facturas", json={"venta_id": venta_id},
                    headers={"X-Tenant-Slug": "pr", "Idempotency-Key": "smoke-1"},
                )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["estado"] == "pendiente"
            factura_id = body["id"]

            # 2) Correr el job del worker (MATIAS mockeado) ---
            ctx: dict = {}
            await on_startup(ctx)
            resultado = await emitir_documento(ctx, tenant_id, factura_id)
            assert resultado == "aceptada"

        # 3) Verificar en la base del tenant ---
        async with AsyncSession(tenant.engine) as s:
            estado, cufe = (await s.execute(
                text("SELECT estado, cufe FROM facturas_electronicas WHERE id=:i"), {"i": factura_id}
            )).one()
        assert estado == "aceptada"
        assert cufe == _CUFE
    finally:
        r = aioredis.from_url(get_settings().redis_url)
        await r.flushdb()
        await r.aclose()
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate("pr")
        get_settings.cache_clear()
        drop_database(control_name)
