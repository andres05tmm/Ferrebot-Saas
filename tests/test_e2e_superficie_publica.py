"""E2E — superficie pública Melquiadez cosida de punta a punta (plan §8, M8). Postgres + Redis.

Valida la cadena que vende, contra el stack real con la app corriendo su LIFESPAN, sobre el flujo
que un prospecto y un cliente nuevo recorren de verdad:

  (a) login de la identidad DEMO por /auth/login/password  → JWT con el claim `tenant` correcto;
  (b) GET /config con Host `barberia-demo.melquiadez.com`  → el resolver resuelve por SUBDOMINIO
      (gana sobre un X-Tenant-Slug señuelo) y devuelve el preset `navaja` + sus packs;
  (c) GET /config con Host `app.melquiadez.com` + ese JWT   → `app` es label RESERVADO (no tenant),
      así que el resolver cae al claim del JWT y resuelve igual (sin esto, el wildcard rompería);
  (d) tools.switch_demo re-apunta el número Kapso a otro tenant y DE VUELTA → `wa_numeros` queda
      consistente (control DB real; Redis falso para la memoria, como en test_switch_demo).

No se overridea nada del flujo: es el resolver real (subdominio → header → claim), el login real
(argon2 + lockout en Redis) y el adaptador psycopg real de switch_demo contra el control DB efímero.
BASE_DOMAIN se fija a `melquiadez.com` SOLO en este test (el resolver lo lee de settings).
"""
import fnmatch
import uuid

import httpx
import psycopg
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row
from sqlalchemy import text

import core.db.session as session_mod
from apps.api.main import create_app, lifespan
from core.auth import decode_token
from core.auth.passwords import hash_password
from core.config import get_settings
from core.crypto import encrypt
from core.db.engine_cache import engine_cache
from core.db.urls import tenant_url, to_libpq
from core.tenancy.branding_presets import PRESETS
from core.tenancy.cache import control_cache
from core.tenancy.resolver import _slug_from_host
from tests.conftest import create_database, drop_database
from tools.switch_demo import DEFAULT_PHONE_NUMBER_ID, PsycopgControlRepo, run

_BASE_DOMAIN = "melquiadez.com"
_DEMO_EMAIL = "demo@barberia-demo.melquiadez.com"
_DEMO_PASSWORD = "navaja-2026"          # contraseña pública corta de la demo (rol vendedor)
_PNID = DEFAULT_PHONE_NUMBER_ID
_PACKS_BARBERIA = {"pack_agenda", "canal_whatsapp", "pack_faq"}


class _FakeRedisSync:
    """Redis sincrónico mínimo para switch_demo: `scan_iter` por glob + `delete` (sin red)."""

    def __init__(self, claves=()):
        self.data = {k: "x" for k in claves}

    def scan_iter(self, match=None):
        for k in list(self.data):
            if match is None or fnmatch.fnmatch(k, match):
                yield k

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                n += 1
        return n


async def _seed_empresa(cs, *, slug: str, nombre: str, plan_id: int, tenant: str, master: str,
                        preset: str) -> int:
    """Empresa activa + su tenant_databases (URL cifrada) + branding por preset. Devuelve empresa_id."""
    eid = (await cs.execute(
        text("INSERT INTO empresas (nombre, nit, slug, estado, plan_id) "
             "VALUES (:n,:nit,:s,'activa',:p) RETURNING id"),
        {"n": nombre, "nit": f"NIT-{uuid.uuid4().hex[:8]}", "s": slug, "p": plan_id},
    )).scalar_one()
    await cs.execute(
        text("INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada, region) "
             "VALUES (:e,:d,'localhost',:u,'local')"),
        {"e": eid, "d": f"db_{slug.replace('-', '_')}", "u": encrypt(tenant, master)},
    )
    await cs.execute(
        text("INSERT INTO branding (empresa_id, preset) VALUES (:e,:p)"),
        {"e": eid, "p": preset},
    )
    return eid


async def _seed_control(master: str, barberia_url: str, clinica_url: str) -> tuple[int, int]:
    """Plan con los 3 packs + 2 tenants demo (barbería/navaja, clínica/aurora) + identidad demo.

    La identidad demo es rol `vendedor` (no admin: que no rompa la demo) y trae su `password_hash`
    argon2 ya seteado (en producción lo fija el enlace set-password). Devuelve (barberia_id, clinica_id).
    """
    async with session_mod.control_session() as cs:
        plan_id = (await cs.execute(
            text("INSERT INTO planes (nombre, limites) VALUES ('Demo', CAST(:l AS JSONB)) RETURNING id"),
            {"l": '{"features": ["pack_agenda", "canal_whatsapp", "pack_faq"]}'},
        )).scalar_one()
        barberia_id = await _seed_empresa(
            cs, slug="barberia-demo", nombre="El Patio", plan_id=plan_id,
            tenant=barberia_url, master=master, preset="navaja",
        )
        clinica_id = await _seed_empresa(
            cs, slug="clinica-demo", nombre="Clínica dental Aurora", plan_id=plan_id,
            tenant=clinica_url, master=master, preset="aurora",
        )
        usuario_id = (await cs.execute(
            text("INSERT INTO identidades (email, empresa_id, usuario_id, rol, password_hash) "
                 "VALUES (:e,:emp,:u,'vendedor',:h) RETURNING usuario_id"),
            {"e": _DEMO_EMAIL, "emp": barberia_id, "u": 501, "h": hash_password(_DEMO_PASSWORD)},
        )).scalar_one()
        assert usuario_id == 501
    return barberia_id, clinica_id


async def test_e2e_superficie_publica(tenant_factory, monkeypatch):
    # Control DB efímero + BASE_DOMAIN del plan, ANTES de instanciar settings/app (patrón e2e_dashboard).
    control_name = f"test_control_pub_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    monkeypatch.setenv("BASE_DOMAIN", _BASE_DOMAIN)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    control_cache.clear()
    await engine_cache.dispose_all()

    # Dos tenants demo reales (aunque /config no abra su base, el control plane los referencia).
    barberia = await tenant_factory()
    clinica = await tenant_factory()

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        assert get_settings().base_domain == _BASE_DOMAIN
        master = get_settings().secrets_master_key
        _barberia_id, clinica_id = await _seed_control(master, barberia.url, clinica.url)

        app = create_app()
        async with lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                # (a) Login DEMO por email/contraseña → JWT con el tenant de la empresa del usuario.
                r = await client.post(
                    "/api/v1/auth/login/password",
                    json={"email": _DEMO_EMAIL, "password": _DEMO_PASSWORD},
                )
                assert r.status_code == 200, r.text
                token = r.json()["token"]
                claims = decode_token(token)
                assert claims["tenant"] == "barberia-demo" and claims["rol"] == "vendedor"
                bearer = {"Authorization": f"Bearer {token}"}

                # (b) Host = subdominio del tenant. Mando un X-Tenant-Slug SEÑUELO (clinica-demo): si
                #     el resolver NO priorizara el subdominio, resolvería clinica y el invariante
                #     claim≠slug daría 403. Un 200 con navaja prueba que el subdominio gana.
                r = await client.get(
                    "/api/v1/config",
                    headers={**bearer, "Host": "barberia-demo.melquiadez.com",
                             "X-Tenant-Slug": "clinica-demo"},
                )
                assert r.status_code == 200, r.text
                cfg = r.json()
                assert cfg["usuario"]["tenant"] == "barberia-demo"
                assert cfg["branding"]["preset"] == "navaja"
                assert cfg["branding"]["tokens"] == PRESETS["navaja"].tokens()
                assert _PACKS_BARBERIA <= set(cfg["features"])

                # (c) Host = app.melquiadez.com (label RESERVADO → el resolver lo trata como "sin
                #     subdominio"). Sin X-Tenant-Slug, la única señal viva es el claim del JWT. Resuelve
                #     barberia-demo igual: el fix de labels reservados deja vivir el wildcard.
                r = await client.get(
                    "/api/v1/config",
                    headers={**bearer, "Host": "app.melquiadez.com"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["usuario"]["tenant"] == "barberia-demo"
                assert r.json()["branding"]["preset"] == "navaja"

        # Verificación directa del resolver (complementa el flujo HTTP, sin ambigüedad):
        assert _slug_from_host("barberia-demo.melquiadez.com", _BASE_DOMAIN) == "barberia-demo"
        assert _slug_from_host("app.melquiadez.com", _BASE_DOMAIN) is None

        # (d) Switch del número Kapso entre demos y DE VUELTA: wa_numeros consistente.
        with psycopg.connect(to_libpq(control_url), row_factory=dict_row, autocommit=True) as conn:
            repo = PsycopgControlRepo(conn)
            fake_redis = _FakeRedisSync(claves=[f"wa:conv:{clinica_id}:+573001112233"])

            # Estado inicial: el número apunta a clinica-demo.
            repo.reapuntar(_PNID, clinica_id)
            assert repo.empresa_actual(_PNID).slug == "clinica-demo"

            # Ida: → barberia-demo (re-apunta y limpia memoria).
            assert run(repo, fake_redis, ["barberia"]) == 0
            assert repo.empresa_actual(_PNID).slug == "barberia-demo"

            # Vuelta: → clinica-demo. El mapeo queda como al inicio (consistente).
            assert run(repo, fake_redis, ["clinica"]) == 0
            actual = repo.empresa_actual(_PNID)
            assert actual.slug == "clinica-demo" and actual.id == clinica_id
            # Una sola fila por número (el switch hace upsert, no inserta duplicados).
            n = conn.execute(
                "SELECT count(*) AS n FROM wa_numeros WHERE phone_number_id = %s", (_PNID,)
            ).fetchone()["n"]
            assert n == 1
    finally:
        redis = aioredis.from_url(get_settings().redis_url)
        await redis.flushdb()
        await redis.aclose()
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.clear()
        get_settings.cache_clear()
        drop_database(control_name)
