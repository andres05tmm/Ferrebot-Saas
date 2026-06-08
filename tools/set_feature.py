"""Encender/apagar una feature (capacidad) de un tenant en el control DB (`empresa_features`).

Helper de operación que reusa la validación del provisioning (`tools.provision_tenant`): valida contra
el catálogo (`core.tenancy.catalogo`) y las dependencias del set EFECTIVO antes de escribir, igual que
`cargar_plan_features`. Idempotente (UPSERT por `(empresa_id, feature)`). Driver sync (psycopg); no
toca el caché de engines async de la API.

Uso:
    python -m tools.set_feature <slug> <feature> [on|off]   # default: on

Imprime el set EFECTIVO de features del tenant (núcleo ∪ plan ± overrides), tal como lo ve el gate.
"""
import argparse
import json
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import to_libpq
from core.tenancy.catalogo import (
    OPCIONALES,
    capacidades_completas,
    es_feature_valida,
    validar_dependencias,
)
from tools.provision_tenant import _features_efectivas


def _validar_catalogo(feature: str) -> None:
    """Valida que la feature sea TOGGLEABLE (existe y es OPCIONAL). PURO: no toca la BD."""
    if not es_feature_valida(feature):
        raise ValueError(f"feature desconocida: '{feature}' (no está en el catálogo)")
    if feature not in OPCIONALES:
        raise ValueError(f"'{feature}' es de núcleo (siempre activa): no se puede encender/apagar")


def _plan_features(conn, plan_id: int | None) -> list[str]:
    """Features del plan de la empresa (planes.limites = {'features': [...]}); [] si no tiene plan."""
    if plan_id is None:
        return []
    row = conn.execute("SELECT limites FROM planes WHERE id=%s", (plan_id,)).fetchone()
    if not row or not row["limites"]:
        return []
    limites = row["limites"] if isinstance(row["limites"], dict) else json.loads(row["limites"])
    return list(limites.get("features", []))


def set_feature(slug: str, feature: str, habilitar: bool = True) -> frozenset[str]:
    """Activa (`habilitar=True`) o desactiva la feature del tenant. Devuelve el set efectivo resultante.

    Valida catálogo y dependencias ANTES de escribir (no deja un estado inválido). Idempotente: re-
    ejecutar con el mismo valor no cambia nada.
    """
    _validar_catalogo(feature)   # antes de abrir conexión (testeable sin Postgres)
    with psycopg.connect(to_libpq(get_settings().control_database_url), row_factory=dict_row) as conn:
        empresa = conn.execute("SELECT id, plan_id FROM empresas WHERE slug=%s", (slug,)).fetchone()
        if empresa is None:
            raise ValueError(f"empresa '{slug}' no existe")
        empresa_id = empresa["id"]

        plan_features = _plan_features(conn, empresa["plan_id"])
        filas = conn.execute(
            "SELECT feature, habilitada FROM empresa_features WHERE empresa_id=%s", (empresa_id,)
        ).fetchall()
        overrides = {f["feature"]: f["habilitada"] for f in filas}
        overrides[feature] = bool(habilitar)

        # Validar dependencias del set efectivo resultante (apagar un requisito que otra feature usa,
        # o encender una feature sin su requisito, se rechaza). Reusa el catálogo del provisioning.
        efectivas = _features_efectivas(plan_features, overrides)
        errores = validar_dependencias(capacidades_completas(efectivas))
        if errores:
            raise ValueError("dependencias de features no satisfechas: " + "; ".join(errores))

        conn.execute(
            "INSERT INTO empresa_features (empresa_id, feature, habilitada) VALUES (%s,%s,%s) "
            "ON CONFLICT (empresa_id, feature) DO UPDATE SET habilitada=EXCLUDED.habilitada",
            (empresa_id, feature, bool(habilitar)),
        )
        conn.commit()
        return capacidades_completas(efectivas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Encender/apagar una feature de un tenant en el control DB (empresa_features)."
    )
    parser.add_argument("slug", help="slug de la empresa")
    parser.add_argument("feature", help="nombre canónico de la feature (catálogo de OPCIONALES)")
    parser.add_argument("estado", nargs="?", default="on", choices=["on", "off"],
                        help="on = activar (default), off = desactivar")
    args = parser.parse_args(argv)

    habilitar = args.estado == "on"
    efectivas = set_feature(args.slug, args.feature, habilitar)
    verbo = "✓ activada" if habilitar else "✗ desactivada"
    print(f"{verbo} '{args.feature}' en '{args.slug}'")
    print("features efectivas: " + ", ".join(sorted(efectivas)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
