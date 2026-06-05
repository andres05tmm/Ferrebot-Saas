"""E7 — smoke end-to-end del dashboard cosido de punta a punta. Requiere Postgres + Redis.

Valida la cadena crítica contra el stack real con la app corriendo su LIFESPAN (el bridge
pg_notify → event_hub/SSE debe estar vivo para que el evento llegue):

  login (Telegram Login Widget firmado) → JWT → GET /reportes/resumen (vacío) → suscribir el bus de
  eventos de la empresa → POST /ventas → recibir 'venta_registrada' → GET /reportes/resumen refleja la venta.

Sin red real: el bot-token va CIFRADO en secretos_empresa (patrón existente) y la firma del widget se
genera con el helper de test_auth_login. No se overridea auth: es el flujo real Bearer/X-Tenant-Slug.

Nota de transporte: la suscripción al stream se hace por `event_hub.subscribe` (el MISMO bus que
alimenta GET /api/v1/events) en vez de leer el endpoint por HTTP, porque httpx.ASGITransport bufferiza
la respuesta hasta completarla y no permite leer un SSE infinito. El SSE por HTTP real se valida en el
smoke manual (docs/fase-11-dashboard/smoke-manual.md).
"""
import asyncio
import json
import uuid

import httpx
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import core.db.session as session_mod
from apps.api.main import create_app, lifespan
from core.auth import decode_token
from core.config import get_settings
from core.config.timezone import now_co
from core.crypto import encrypt, encrypt_split
from core.db.engine_cache import engine_cache
from core.db.urls import tenant_url
from core.events.hub import event_hub
from core.tenancy.cache import control_cache
from tests.conftest import create_database, drop_database
from tests.test_auth_login import _firmar  # helper de firma del Telegram Login Widget

_BOT_TOKEN = "999000:E2E-DashboardBotToken"
_TELEGRAM_ID = 778899


def _payload_firmado(telegram_id: int, bot_token: str) -> dict:
    """Payload del widget firmado con `bot_token` y fresco (auth_date = ahora Colombia)."""
    base = {
        "id": telegram_id, "first_name": "Admin", "username": "admin",
        "auth_date": int(now_co().timestamp()),
    }
    base["hash"] = _firmar(base, bot_token)
    return base


async def _seed_control(master: str, db_name: str, tenant_url_base: str, bot_token: str) -> int:
    """Empresa 'pr' activa + plan + bot-token CIFRADO en secretos_empresa + tenant_databases."""
    async with session_mod.control_session() as cs:
        pid = (await cs.execute(
            text("INSERT INTO planes (nombre, limites) VALUES ('Pro', CAST(:l AS JSONB)) RETURNING id"),
            {"l": "{}"},
        )).scalar_one()
        eid = (await cs.execute(
            text("INSERT INTO empresas (nombre, nit, slug, estado, plan_id) "
                 "VALUES ('Punto Rojo','900','pr','activa',:p) RETURNING id"), {"p": pid},
        )).scalar_one()
        tok_ct, tok_n = encrypt_split(bot_token, master)
        await cs.execute(
            text("INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
                 "VALUES (:e,'telegram_token',:c,:n)"),
            {"e": eid, "c": tok_ct, "n": tok_n},
        )
        await cs.execute(
            text("INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada, region) "
                 "VALUES (:e,:d,'localhost',:u,'local')"),
            {"e": eid, "d": db_name, "u": encrypt(tenant_url_base, master)},
        )
    return eid


async def _seed_tenant(engine, telegram_id: int) -> int:
    """Usuario admin con telegram_id conocido + producto con inventario. Devuelve producto_id."""
    async with AsyncSession(engine) as s:
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol, telegram_id, activo) "
                 "VALUES ('Admin','admin',:t,true)"), {"t": telegram_id},
        )
        pid = (await s.execute(
            text("INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                 "VALUES ('Martillo','unidad',11900,19,false,true) RETURNING id"))).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,100,0)"),
            {"p": pid},
        )
        await s.commit()
    return pid


async def test_e2e_dashboard_login_venta_sse(tenant, monkeypatch):
    # Control DB efímero: rutea control_session() al efímero (patrón de test_e2e_facturacion).
    control_name = f"test_control_dash_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    control_cache.invalidate("pr")
    # Evita reusar un engine cacheado de id=1 de otro test E2E (engine_cache keyea por tenant_id).
    await engine_cache.dispose_all()

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        master = get_settings().secrets_master_key
        eid = await _seed_control(master, tenant.name, tenant.url, _BOT_TOKEN)
        producto_id = await _seed_tenant(tenant.engine, _TELEGRAM_ID)

        app = create_app()
        async with lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                # 5) Login por Telegram Login Widget → JWT con claims correctos.
                r = await client.post(
                    "/api/v1/auth/login",
                    json=_payload_firmado(_TELEGRAM_ID, _BOT_TOKEN),
                    headers={"X-Tenant-Slug": "pr"},
                )
                assert r.status_code == 200, r.text
                token = r.json()["token"]
                claims = decode_token(token)
                assert claims["tenant"] == "pr" and claims["rol"] == "admin"
                auth = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": "pr"}

                # 6) Resumen del día vacío → ceros.
                r = await client.get("/api/v1/reportes/resumen", headers=auth)
                assert r.status_code == 200, r.text
                assert r.json()["num_ventas"] == 0

                # 7) Suscribir el bus de la empresa ANTES de vender (el orden importa: pg_notify solo
                #    llega a los LISTEN vivos). Luego vender por HTTP y esperar el evento.
                queue = await event_hub.subscribe(tenant_id=eid, dsn=tenant.url)
                try:
                    r = await client.post(
                        "/api/v1/ventas",
                        json={"metodo_pago": "efectivo", "lineas": [{"producto_id": producto_id, "cantidad": 1}]},
                        headers={**auth, "Idempotency-Key": "e2e-dash-1"},
                    )
                    assert r.status_code == 201, r.text

                    payload = await asyncio.wait_for(queue.get(), timeout=10.0)
                    evento = json.loads(payload)
                    assert evento["event"] == "venta_registrada"
                    assert evento["data"]["total"] == "11900.00"
                finally:
                    await event_hub.unsubscribe(eid, queue)

                # 8) Cierre: el resumen ahora refleja la venta.
                r = await client.get("/api/v1/reportes/resumen", headers=auth)
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["num_ventas"] == 1
                assert float(body["total_vendido"]) > 0
    finally:
        redis = aioredis.from_url(get_settings().redis_url)
        await redis.flushdb()
        await redis.aclose()
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate("pr")
        get_settings.cache_clear()
        drop_database(control_name)
