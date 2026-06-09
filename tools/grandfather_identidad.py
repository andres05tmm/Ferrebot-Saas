"""Siembra la identidad de login del admin de un tenant YA existente (login real, ADR 0009 A1.4).

Para los tenants creados antes del login real (puntorojo, clinica-demo): toma el usuario admin que ya
existe en la base del tenant y crea su `identidad` en el control DB, emitiendo un token de set-password
para entrega manual. El email se RECIBE como parámetro (no se inventa). Idempotente (no toca la data de
negocio del tenant; re-correr no duplica la identidad —upsert por email—).

    python -m tools.grandfather_identidad <slug> <email>
"""
from __future__ import annotations

import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.logging import configure_logging, get_logger
from tools.provision_tenant import (
    _db_name,
    admin_usuario_id,
    crear_identidad_admin,
    emitir_token_set_password,
)

log = get_logger("grandfather_identidad")


def _empresa_id(slug: str) -> int | None:
    with psycopg.connect(to_libpq(get_settings().control_database_url), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id FROM empresas WHERE slug = %s", (slug,)).fetchone()
        return row["id"] if row else None


def grandfather(slug: str, email: str) -> tuple[int, str | None]:
    """Crea la identidad del admin del tenant `slug` con `email`. Devuelve (identidad_id, token|None)."""
    empresa_id = _empresa_id(slug)
    if empresa_id is None:
        raise ValueError(f"empresa '{slug}' no existe en el control DB")
    conn_url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
    usuario_id = admin_usuario_id(conn_url)
    if usuario_id is None:
        raise ValueError(f"el tenant '{slug}' no tiene usuario admin en su base")
    identidad_id = crear_identidad_admin(empresa_id, usuario_id, email)
    token = emitir_token_set_password(identidad_id)
    log.info("grandfather_identidad", slug=slug, empresa_id=empresa_id, identidad_id=identidad_id)
    return identidad_id, token


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    configure_logging()
    parser = argparse.ArgumentParser(description="Sembrar la identidad de login de un tenant existente.")
    parser.add_argument("slug", help="slug del tenant (control DB)")
    parser.add_argument("email", help="email del admin (se RECIBE; no se inventa)")
    args = parser.parse_args(argv)

    try:
        identidad_id, token = grandfather(args.slug, args.email)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"identidad de {args.email} sembrada para '{args.slug}' (id={identidad_id})")
    if token:
        print(f"   set-password: token={token}")
    else:
        print("   Redis no disponible para el token; el admin puede usar 'olvidé mi contraseña'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
