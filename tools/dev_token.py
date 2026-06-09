"""Helper SOLO de desarrollo: emite un JWT de admin para entrar al dashboard SIN el Telegram Login.

    python -m tools.dev_token [slug]      # default: clinica-demo

Resuelve el tenant por slug (control DB), lee el id del usuario admin en SU base, y firma el JWT con
`core.auth.jwt.create_access_token` (mismos claims que el login real: sub/tenant/rol). Imprime EXACTO
qué poner en `localStorage` (claves de `lib/api.js`: `ferrebot_token` y el objeto usuario `ferrebot_user`,
con la forma que guarda `hooks/useAuth.js` tras el login).

NO toca producción: solo lee el control DB y la base del tenant; no escribe nada.
"""
import argparse
import json
import sys

import psycopg
from psycopg.rows import dict_row

from core.auth.jwt import create_access_token
from core.config import get_settings
from core.crypto import decrypt
from core.db.urls import to_libpq

# Claves de localStorage (lib/api.js): el token y el objeto usuario {id, rol, tenant} (useAuth.js).
TOKEN_KEY = "ferrebot_token"
USER_KEY = "ferrebot_user"


def _tenant(control_url: str, slug: str, master: str) -> tuple[str, str] | None:
    """(connection_url descifrada, estado) del tenant, o None si el slug no existe."""
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT e.estado, t.connection_url_cifrada FROM empresas e "
            "JOIN tenant_databases t ON t.empresa_id = e.id WHERE e.slug = %s",
            (slug,),
        ).fetchone()
    if row is None:
        return None
    return decrypt(bytes(row["connection_url_cifrada"]), master), row["estado"]


def _admin_user_id(tenant_url: str) -> int | None:
    with psycopg.connect(to_libpq(tenant_url)) as conn:
        row = conn.execute("SELECT id FROM usuarios WHERE rol = 'admin' ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):   # consola Windows cp1252 → forzar UTF-8
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Emite un JWT de admin para el dashboard (solo dev).")
    parser.add_argument("slug", nargs="?", default="clinica-demo")
    args = parser.parse_args(argv)
    slug = args.slug

    settings = get_settings()
    resuelto = _tenant(settings.control_database_url, slug, settings.secrets_master_key)
    if resuelto is None:
        print(f"ERROR: no existe la empresa con slug '{slug}' en el control DB.", file=sys.stderr)
        print("       Corre primero: python -m tools.provision_from_manifest "
              "--from tools/onboarding/clinica-demo.manifest.example.yaml", file=sys.stderr)
        return 1
    tenant_url, estado = resuelto
    if estado != "activa":
        print(f"AVISO: la empresa '{slug}' está '{estado}' (no 'activa'): el API la rechazará.", file=sys.stderr)

    user_id = _admin_user_id(tenant_url)
    if user_id is None:
        print(f"ERROR: no hay usuario admin en la base de '{slug}'.", file=sys.stderr)
        return 1

    token = create_access_token(user_id=user_id, tenant=slug, rol="admin")
    usuario = {"id": user_id, "rol": "admin", "tenant": slug}
    usuario_json = json.dumps(usuario, ensure_ascii=False)

    print(f"\nTenant: {slug}  ·  usuario admin id={user_id}  ·  expira en {settings.jwt_expire_minutes} min\n")
    print("Pega esto en la CONSOLA del navegador (en http://localhost:5173) y recarga:\n")
    print(f"  localStorage.setItem('{TOKEN_KEY}', '{token}');")
    print(f"  localStorage.setItem('{USER_KEY}', '{usuario_json}');")
    print("  location.href = '/agenda';\n")
    print("Una sola línea (copiar/pegar):")
    print(
        f"localStorage.setItem('{TOKEN_KEY}','{token}');"
        f"localStorage.setItem('{USER_KEY}','{usuario_json}');location.href='/agenda';\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
