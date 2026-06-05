"""Verificación READ-ONLY de un tenant provisionado. NO imprime secretos. NO emite facturas.

Pasos: (1) presencia de carga en control/tenant DB, (2) login real (bot-token descifrado del control
DB; el valor nunca se imprime), (3) catálogos MATIAS producción (países/ciudades, solo lectura).
Uso: .venv/Scripts/python.exe -m tools.verify_tenant [slug]   (default: puntorojo)
"""
import asyncio
import hashlib
import hmac
import json
import sys
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from apps.api.main import create_app, lifespan
from apps.bot.repos import ControlSecretosBot
from core.auth import decode_token
from core.config import get_settings
from core.config.timezone import now_co
from core.crypto import decrypt
from core.db.session import control_session
from core.db.urls import to_libpq
from core.tenancy.capacidades import ControlCapacidades

_NEED_SEC = {"telegram_token", "matias_email", "matias_password"}
_NEED_CFG = {"matias_base_url", "matias_resolution", "matias_prefix", "matias_notes", "matias_city_id"}


def _firmar(datos: dict, bot_token: str) -> str:
    """Firma del Telegram Login Widget (mismo algoritmo que tests/test_auth_login._firmar)."""
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(datos.items()) if k != "hash")
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    return hmac.new(secret, dcs.encode("utf-8"), hashlib.sha256).hexdigest()


async def verificar(slug: str) -> int:
    settings = get_settings()
    master = settings.secrets_master_key

    # --- Paso 1: presencia en control DB (solo PRESENCIA de secretos, nunca valores) ---
    print(f"== [1] Carga en control DB (slug={slug}) ==")
    with psycopg.connect(to_libpq(settings.control_database_url), row_factory=dict_row) as conn:
        emp = conn.execute("SELECT id, slug, estado FROM empresas WHERE slug=%s", (slug,)).fetchone()
        if not emp:
            print(f"  empresa slug={slug}: NO ENCONTRADA -> nada que verificar (revisa CONTROL_DATABASE_URL)")
            return 1
        eid = emp["id"]
        print(f"  empresa: id={eid} slug={emp['slug']} estado={emp['estado']}  [{'OK' if emp['estado'] == 'activa' else 'NO'}]")
        sec = {r["clave"] for r in conn.execute(
            "SELECT clave FROM secretos_empresa WHERE empresa_id=%s", (eid,)).fetchall()}
        print(f"  secretos: {sorted(_NEED_SEC & sec)}  [{'OK' if _NEED_SEC <= sec else 'FALTAN ' + str(sorted(_NEED_SEC - sec))}]")
        cfg = {r["clave"] for r in conn.execute(
            "SELECT clave FROM config_empresa WHERE empresa_id=%s", (eid,)).fetchall()}
        print(f"  config:   {sorted(_NEED_CFG & cfg)}  [{'OK' if _NEED_CFG <= cfg else 'FALTAN ' + str(sorted(_NEED_CFG - cfg))}]")
        br = conn.execute(
            "SELECT color_primario, nombre_comercial FROM branding WHERE empresa_id=%s", (eid,)).fetchone()
        print(f"  branding: [{'OK (' + str(br['color_primario']) + ', ' + str(br['nombre_comercial']) + ')' if br else 'NO'}]")
        tdb = conn.execute(
            "SELECT connection_url_cifrada FROM tenant_databases WHERE empresa_id=%s", (eid,)).fetchone()

    if not tdb:
        print("  tenant_databases: NO registrado -> no puedo abrir la base del tenant")
        return 1
    tenant_url_ = decrypt(bytes(tdb["connection_url_cifrada"]), master)

    # admin.telegram_id en la base del tenant (no se imprime el valor)
    with psycopg.connect(to_libpq(tenant_url_), row_factory=dict_row) as tconn:
        admin = tconn.execute(
            "SELECT telegram_id FROM usuarios WHERE rol='admin' ORDER BY id LIMIT 1").fetchone()
    tg_db = admin["telegram_id"] if admin else None
    json_path = Path(f"tools/onboarding/{slug}.json")
    tg_json = None
    if json_path.exists():
        tg_json = json.loads(json_path.read_text(encoding="utf-8")).get("admin", {}).get("telegram_id")
    if tg_db is None:
        print("  admin.telegram_id: NO seteado")
    elif tg_json is None:
        print(f"  admin.telegram_id: presente (sin {json_path.name} para comparar)")
    else:
        print(f"  admin.telegram_id == JSON: [{'OK' if tg_db == tg_json else 'NO'}]")

    # capacidades efectivas (preempt: el gate fiscal es 404 si no está activa)
    async with control_session() as cs:
        efectivas = await ControlCapacidades(cs).efectivas(eid)
        bot_token = await ControlSecretosBot(cs, master).bot_token(eid)
    fe = "facturacion_electronica" in efectivas
    print(f"  facturacion_electronica activa: [{'SI' if fe else 'NO (los catálogos fiscales darán 404)'}]")

    if tg_db is None or not bot_token:
        print("  -> falta telegram_id o bot-token; no puedo hacer el login real")
        return 1

    # --- Pasos 2 y 3: login real + catálogos MATIAS (app con lifespan + ASGITransport) ---
    app = create_app()
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30) as client:
            payload = {"id": tg_db, "first_name": "Verify", "username": "verify",
                       "auth_date": int(now_co().timestamp())}
            payload["hash"] = _firmar(payload, bot_token)
            print("== [2] Login real ==")
            r = await client.post("/api/v1/auth/login", json=payload, headers={"X-Tenant-Slug": slug})
            print(f"  POST /auth/login -> {r.status_code}")
            if r.status_code != 200:
                print(f"  body: {r.text[:300]}")
                return 1
            claims = decode_token(r.json()["token"])
            ok = claims.get("tenant") == slug and claims.get("rol") == "admin"
            print(f"  JWT tenant={claims.get('tenant')} rol={claims.get('rol')}  [{'OK' if ok else 'NO'}]")
            auth = {"Authorization": f"Bearer {r.json()['token']}", "X-Tenant-Slug": slug}

            print("== [3] Catálogos MATIAS (producción, read-only) ==")
            for path, label in (("/api/v1/clientes/paises", "paises"),
                                ("/api/v1/clientes/ciudades?q=cartagena", "ciudades?q=cartagena")):
                try:
                    rr = await client.get(path, headers=auth)
                except Exception as exc:  # noqa: BLE001 — reportar el error exacto sin secretos
                    print(f"  {label}: ERROR {type(exc).__name__}: {exc}")
                    continue
                if rr.status_code != 200:
                    print(f"  {label}: status={rr.status_code} body={rr.text[:500]}")
                    continue
                data = rr.json()
                ejemplos = [d.get("nombre") for d in data[:3]]
                print(f"  {label}: 200, n={len(data)}, ejemplos={ejemplos}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    slug = argv[0] if argv else "puntorojo"
    return asyncio.run(verificar(slug))


if __name__ == "__main__":
    sys.exit(main())
