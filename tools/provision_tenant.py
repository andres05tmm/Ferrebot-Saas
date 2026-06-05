"""Aprovisionar una empresa (tenancy.md §8). Idempotente: se puede reintentar.

CREATE DATABASE -> registrar en control (URL cifrada) -> migrar -> sembrar -> admin -> activa.
Usa driver sync (psycopg); no toca el caché de engines async de la API.
"""
import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.crypto import encrypt, encrypt_split
from core.db.urls import tenant_url, to_libpq
from core.tenancy.catalogo import (
    capacidades_completas,
    es_feature_valida,
    validar_dependencias,
)
from tools._alembic import upgrade_tenant

# Claves CIFRADAS en secretos_empresa (las lee ControlSecretosBot / cargar_config_matias).
_CLAVES_SECRETAS = ("telegram_token", "matias_email", "matias_password")
# Claves EN CLARO en config_empresa (las lee cargar_config_matias; nombres exactos).
_CLAVES_CONFIG = (
    "matias_base_url", "matias_resolution", "matias_prefix", "matias_notes", "matias_city_id",
    "matias_ambiente",
)
# Cloudinary (bloque `cloudinary` del onboarding): api_key/api_secret CIFRADOS, cloud_name en claro.
# (clave en el JSON → clave en secretos_empresa) — las lee `cargar_config_cloudinary`.
_CLAVES_CLOUDINARY_SECRETAS = (
    ("api_key", "cloudinary_api_key"),
    ("api_secret", "cloudinary_api_secret"),
)


def _db_name(slug: str) -> str:
    return f"ferrebot_{slug}"


def _create_database(admin_url: str, db_name: str) -> None:
    with psycopg.connect(to_libpq(admin_url), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')


def _register_control(control_url: str, *, slug, nombre, nit, db_name, host, conn_cifrada) -> int:
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id FROM empresas WHERE slug = %s", (slug,)).fetchone()
        if row:
            empresa_id = row["id"]
            conn.execute("UPDATE empresas SET nombre=%s, nit=%s WHERE id=%s", (nombre, nit, empresa_id))
        else:
            empresa_id = conn.execute(
                "INSERT INTO empresas (nombre, nit, slug, estado) VALUES (%s,%s,%s,'provisionando') RETURNING id",
                (nombre, nit, slug),
            ).fetchone()["id"]
        conn.execute(
            """INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (empresa_id) DO UPDATE
               SET db_name=EXCLUDED.db_name, host=EXCLUDED.host,
                   connection_url_cifrada=EXCLUDED.connection_url_cifrada""",
            (empresa_id, db_name, host, conn_cifrada),
        )
        conn.commit()
        return empresa_id


def _seed(tenant_url_: str, admin_nombre: str) -> None:
    with psycopg.connect(to_libpq(tenant_url_)) as conn:
        existe = conn.execute("SELECT 1 FROM usuarios WHERE rol='admin' LIMIT 1").fetchone()
        if not existe:
            conn.execute("INSERT INTO usuarios (nombre, rol) VALUES (%s,'admin')", (admin_nombre,))
        # La config no-secreta por empresa vive en el CONTROL DB (config_empresa con empresa_id),
        # no en la app DB (tabla retirada en tenant 0005). No se siembra nada aquí.
        conn.commit()


def _activar(control_url: str, empresa_id: int) -> None:
    with psycopg.connect(to_libpq(control_url), autocommit=True) as conn:
        conn.execute("UPDATE empresas SET estado='activa' WHERE id=%s", (empresa_id,))


def provision_tenant(slug: str, nombre: str, nit: str, admin_nombre: str = "Admin") -> int:
    """Aprovisiona la empresa y devuelve su empresa_id. Idempotente."""
    settings = get_settings()
    db_name = _db_name(slug)
    host = settings.tenants_direct_url_base
    conn_url = tenant_url(settings.tenants_direct_url_base, db_name)

    _create_database(settings.admin_database_url, db_name)
    cifrada = encrypt(conn_url, settings.secrets_master_key)
    empresa_id = _register_control(
        settings.control_database_url, slug=slug, nombre=nombre, nit=nit,
        db_name=db_name, host=host, conn_cifrada=cifrada,
    )
    upgrade_tenant(conn_url)
    _seed(conn_url, admin_nombre)
    _activar(settings.control_database_url, empresa_id)
    return empresa_id


def cargar_secretos_empresa(empresa_id: int, datos: dict) -> None:
    """UPSERT idempotente (control DB) de secretos cifrados + config en claro + branding de una empresa.

    - `secretos`   → `secretos_empresa` CIFRADO con `secrets_master_key` (encrypt_split; nunca en claro).
    - `config`     → `config_empresa` en claro (claves exactas que lee `cargar_config_matias`).
    - `cloudinary` → api_key/api_secret CIFRADOS en `secretos_empresa`; cloud_name en `config_empresa`.
    - `branding`   → tabla `branding` (lo lee `control_repo.leer_branding`).
    Claves ausentes en `datos` se omiten; re-ejecutar no duplica (ON CONFLICT).
    """
    settings = get_settings()
    master = settings.secrets_master_key
    secretos = datos.get("secretos", {})
    config = datos.get("config", {})
    cloudinary = datos.get("cloudinary", {})
    branding = datos.get("branding")

    def _upsert_secreto(conn, clave: str, valor: str) -> None:
        cifrado, nonce = encrypt_split(str(valor), master)
        conn.execute(
            "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (empresa_id, clave) DO UPDATE "
            "SET valor_cifrado=EXCLUDED.valor_cifrado, nonce=EXCLUDED.nonce, actualizado_en=now()",
            (empresa_id, clave, cifrado, nonce),
        )

    def _upsert_config(conn, clave: str, valor: str) -> None:
        conn.execute(
            "INSERT INTO config_empresa (empresa_id, clave, valor) VALUES (%s,%s,%s) "
            "ON CONFLICT (empresa_id, clave) DO UPDATE SET valor=EXCLUDED.valor, actualizado_en=now()",
            (empresa_id, clave, str(valor)),
        )

    with psycopg.connect(to_libpq(settings.control_database_url)) as conn:
        for clave in _CLAVES_SECRETAS:
            if secretos.get(clave) is not None:
                _upsert_secreto(conn, clave, secretos[clave])
        for clave in _CLAVES_CONFIG:
            if config.get(clave) is not None:
                _upsert_config(conn, clave, config[clave])
        # Cloudinary: secretos cifrados + cloud_name en claro (claves ausentes se omiten).
        for clave_json, clave_db in _CLAVES_CLOUDINARY_SECRETAS:
            if cloudinary.get(clave_json) is not None:
                _upsert_secreto(conn, clave_db, cloudinary[clave_json])
        if cloudinary.get("cloud_name") is not None:
            _upsert_config(conn, "cloudinary_cloud_name", cloudinary["cloud_name"])
        if branding:
            conn.execute(
                "INSERT INTO branding (empresa_id, logo_url, color_primario, nombre_comercial, dominio) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (empresa_id) DO UPDATE "
                "SET logo_url=EXCLUDED.logo_url, color_primario=EXCLUDED.color_primario, "
                "nombre_comercial=EXCLUDED.nombre_comercial, dominio=EXCLUDED.dominio",
                (empresa_id, branding.get("logo_url"), branding.get("color_primario") or "#C8200E",
                 branding.get("nombre_comercial"), branding.get("dominio")),
            )
        conn.commit()


def _features_efectivas(plan_features: list[str], overrides: dict) -> frozenset[str]:
    """Set efectivo: features del plan ± overrides (true añade, false quita). PURO."""
    efectivas = set(plan_features)
    for feature, habilitada in overrides.items():
        if habilitada:
            efectivas.add(feature)
        else:
            efectivas.discard(feature)
    return frozenset(efectivas)


def cargar_plan_features(empresa_id: int, datos: dict) -> None:
    """Asigna plan y features a la empresa desde el JSON, VALIDANDO antes de escribir. Idempotente.

    - `plan`: UPSERT por NOMBRE (planes.limites = {"features": [...]}) + set empresas.plan_id.
    - `features_override`: UPSERT en empresa_features (ON CONFLICT (empresa_id, feature)).
    Valida que toda feature exista (`es_feature_valida`) y que las dependencias del set EFECTIVO se
    cumplan (`validar_dependencias` sobre `capacidades_completas`); si no, lanza ValueError y NO escribe.

    CAVEAT: el plan se upserta por NOMBRE (tier compartido); cambiar sus features afecta a otras
    empresas del mismo plan. La consistencia es responsabilidad del operador.
    """
    plan = datos.get("plan")
    overrides = datos.get("features_override") or {}
    plan_features = list((plan or {}).get("features", []))
    if plan is None and not overrides:
        return  # empresa solo-núcleo: nada que asignar

    # --- Validación ANTES de escribir (no dejar un estado inválido) ---
    for feature in [*plan_features, *overrides.keys()]:
        if not es_feature_valida(feature):
            raise ValueError(f"feature desconocida en onboarding: '{feature}'")
    errores = validar_dependencias(capacidades_completas(_features_efectivas(plan_features, overrides)))
    if errores:
        raise ValueError("dependencias de features no satisfechas: " + "; ".join(errores))

    # --- Escritura idempotente ---
    with psycopg.connect(to_libpq(get_settings().control_database_url), row_factory=dict_row) as conn:
        if plan is not None:
            nombre = plan.get("nombre") or "Custom"
            limites = json.dumps({"features": plan_features})
            row = conn.execute("SELECT id FROM planes WHERE nombre=%s", (nombre,)).fetchone()
            if row:
                plan_id = row["id"]
                conn.execute("UPDATE planes SET limites=CAST(%s AS JSONB) WHERE id=%s", (limites, plan_id))
            else:
                plan_id = conn.execute(
                    "INSERT INTO planes (nombre, limites) VALUES (%s, CAST(%s AS JSONB)) RETURNING id",
                    (nombre, limites),
                ).fetchone()["id"]
            conn.execute("UPDATE empresas SET plan_id=%s WHERE id=%s", (plan_id, empresa_id))
        for feature, habilitada in overrides.items():
            conn.execute(
                "INSERT INTO empresa_features (empresa_id, feature, habilitada) VALUES (%s,%s,%s) "
                "ON CONFLICT (empresa_id, feature) DO UPDATE SET habilitada=EXCLUDED.habilitada",
                (empresa_id, feature, bool(habilitada)),
            )
        conn.commit()


def _set_admin_telegram(tenant_url_: str, telegram_id: int) -> None:
    """Asigna el `telegram_id` real al usuario admin sembrado (idempotente)."""
    with psycopg.connect(to_libpq(tenant_url_)) as conn:
        conn.execute("UPDATE usuarios SET telegram_id=%s WHERE rol='admin'", (telegram_id,))
        conn.commit()


def provision_tenant_full(datos: dict) -> int:
    """Aprovisiona desde un dict de onboarding: base + control + secretos/config/branding + admin.

    Envuelve `provision_tenant` y cierra el hueco (secretos cifrados, config fiscal, branding y el
    `telegram_id` del admin). Idempotente de punta a punta.
    """
    settings = get_settings()
    admin = datos.get("admin", {})
    empresa_id = provision_tenant(
        datos["slug"], datos["nombre"], datos["nit"], admin_nombre=admin.get("nombre", "Admin"),
    )
    cargar_plan_features(empresa_id, datos)   # valida catálogo/dependencias antes de escribir
    cargar_secretos_empresa(empresa_id, datos)
    telegram_id = admin.get("telegram_id")
    if telegram_id is not None:
        conn_url = tenant_url(settings.tenants_direct_url_base, _db_name(datos["slug"]))
        _set_admin_telegram(conn_url, telegram_id)
    return empresa_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aprovisionar una empresa (tenant).")
    parser.add_argument("--from", dest="archivo", help="Ruta a un JSON de onboarding (provisioning completo)")
    parser.add_argument("slug", nargs="?")
    parser.add_argument("nombre", nargs="?")
    parser.add_argument("nit", nargs="?")
    parser.add_argument("--admin", default="Admin", help="Nombre del usuario admin inicial")
    args = parser.parse_args(argv)

    if args.archivo:
        datos = json.loads(Path(args.archivo).read_text(encoding="utf-8"))
        empresa_id = provision_tenant_full(datos)
        slug = datos["slug"]
    else:
        if not (args.slug and args.nombre and args.nit):
            parser.error("se requieren slug, nombre y nit (o --from <archivo.json>)")
        empresa_id = provision_tenant(args.slug, args.nombre, args.nit, admin_nombre=args.admin)
        slug = args.slug

    print(f"empresa '{slug}' aprovisionada (id={empresa_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
