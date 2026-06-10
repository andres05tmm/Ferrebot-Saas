"""Escribir una clave de configuración NO secreta de un tenant en el control DB (`config_empresa`).

Molde de `tools.set_feature`: driver sync (psycopg), UPSERT idempotente por `(empresa_id, clave)`. Lo
usa el switch-on del POS electrónico (claves `matias_resolution_pos`, `matias_prefix_pos`, etc., F2.2) y
cualquier override no secreto por empresa. Los SECRETOS NO van aquí: van CIFRADOS en `secretos_empresa`
(security.md) — esta tool rechaza las claves que parezcan secreto.

Uso:
    python -m tools.set_config <slug> <clave> <valor>

Imprime la confirmación. Para leer, consultar `config_empresa` directamente o el loader del dominio.
"""
import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import to_libpq

# Sufijos/nombres que delatan un secreto: jamás en claro en `config_empresa` (van cifrados aparte).
_PALABRAS_SECRETO = ("password", "secret", "token", "api_key", "apikey", "clave_secreta")


def _es_clave_secreto(clave: str) -> bool:
    """True si la clave parece un secreto (debe ir cifrada en `secretos_empresa`, no en `config_empresa`)."""
    c = clave.lower()
    return any(p in c for p in _PALABRAS_SECRETO)


def set_config(slug: str, clave: str, valor: str) -> None:
    """UPSERT `config_empresa[(empresa, clave)] = valor`. Idempotente. Rechaza claves que parezcan secreto."""
    if _es_clave_secreto(clave):
        raise ValueError(
            f"'{clave}' parece un secreto: va CIFRADO en secretos_empresa, no en config_empresa (security.md)"
        )
    with psycopg.connect(to_libpq(get_settings().control_database_url), row_factory=dict_row) as conn:
        empresa = conn.execute("SELECT id FROM empresas WHERE slug=%s", (slug,)).fetchone()
        if empresa is None:
            raise ValueError(f"empresa '{slug}' no existe")
        conn.execute(
            "INSERT INTO config_empresa (empresa_id, clave, valor) VALUES (%s,%s,%s) "
            "ON CONFLICT (empresa_id, clave) DO UPDATE SET valor=EXCLUDED.valor, actualizado_en=now()",
            (empresa["id"], clave, valor),
        )
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Escribir una clave de config no secreta de un tenant (config_empresa)."
    )
    parser.add_argument("slug", help="slug de la empresa")
    parser.add_argument("clave", help="clave de configuración (p. ej. matias_resolution_pos)")
    parser.add_argument("valor", help="valor en claro (NO secretos)")
    args = parser.parse_args(argv)

    set_config(args.slug, args.clave, args.valor)
    print(f"✓ config '{args.clave}' = '{args.valor}' en '{args.slug}'")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
