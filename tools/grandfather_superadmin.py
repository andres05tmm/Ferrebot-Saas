"""Siembra la PRIMERA identidad de plataforma (super-admin) — ADR 0010 §D2.

El super-admin (operador SaaS, Andrés) es una identidad de PLATAFORMA: sin empresa (`empresa_id` NULL),
`rol = super_admin`. Como no nace de un tenant, su `usuario_id` es un centinela (0): no apunta a ninguna
fila `usuarios` de ningún tenant (las rutas /admin no usan ese id, solo el rol). Crea la identidad SIN
contraseña y emite su token de set-password para entrega manual (reusa el patrón de
tools/grandfather_identidad + clave_pwtoken). Idempotente (upsert por email; re-correr no duplica).

    python -m tools.grandfather_superadmin <email>
"""
from __future__ import annotations

import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import to_libpq
from core.logging import configure_logging, get_logger
from tools.provision_tenant import emitir_token_set_password

log = get_logger("grandfather_superadmin")

# Centinela de usuario para identidades de plataforma: no hay usuario de tenant detrás (ADR 0010 §D2).
_USUARIO_PLATAFORMA = 0


def crear_identidad_superadmin(email: str) -> int:
    """Crea/actualiza la identidad de plataforma (empresa_id NULL, rol super_admin). Idempotente por email.

    El CHECK `ck_identidades_rol_empresa` (migración 0006) exige super_admin ⇒ empresa_id NULL. Devuelve
    el `identidad_id`. `password_hash` queda NULL (el admin la fija por el enlace de set-password).
    """
    with psycopg.connect(to_libpq(get_settings().control_database_url), row_factory=dict_row) as conn:
        row = conn.execute(
            "INSERT INTO identidades (email, empresa_id, usuario_id, rol) "
            "VALUES (%s, NULL, %s, 'super_admin') "
            "ON CONFLICT (lower(email)) DO UPDATE SET empresa_id = NULL, rol = 'super_admin', "
            "actualizado_en = now() RETURNING id",
            (email.strip().lower(), _USUARIO_PLATAFORMA),
        ).fetchone()
        conn.commit()
        return row["id"]


def grandfather_superadmin(email: str) -> tuple[int, str | None]:
    """Siembra la identidad super_admin de `email` y emite su token. Devuelve (identidad_id, token|None)."""
    identidad_id = crear_identidad_superadmin(email)
    token = emitir_token_set_password(identidad_id)
    log.info("grandfather_superadmin", identidad_id=identidad_id)
    return identidad_id, token


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    configure_logging()
    parser = argparse.ArgumentParser(description="Sembrar la primera identidad super-admin (plataforma).")
    parser.add_argument("email", help="email del super-admin (operador SaaS)")
    args = parser.parse_args(argv)

    identidad_id, token = grandfather_superadmin(args.email)
    print(f"identidad super-admin de {args.email} sembrada (id={identidad_id})")
    if token:
        print(f"   set-password: token={token}")
    else:
        print("   Redis no disponible para el token; usa 'olvidé mi contraseña' tras configurar Redis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
