"""ETL FerreBot → tenant Punto Rojo. Orquesta extract → transform → load → sequences (spec §4).

Uso:
    python -m tools.etl_puntorojo --origen-url postgresql://... --slug puntorojo [--limpiar] [--dry-run]

- `--origen-url`: dump del legacy restaurado localmente o réplica (NUNCA la prod viva).
- `--limpiar`: barre las tablas del ETL en el destino antes de cargar (necesario si el tenant
  tiene seeds del manifiesto; loguea el radio de la cascada).
- `--dry-run`: extrae y transforma, reporta conteos, no toca el destino.

La validación de paridad completa es el módulo hermano:
    python -m tools.etl_puntorojo.verify --origen-url ... --slug puntorojo
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.config import get_settings
from core.db.urls import tenant_url
from core.logging import configure_logging, get_logger
from tools.etl_puntorojo.extract import leer_origen
from tools.etl_puntorojo.load import cargar
from tools.etl_puntorojo.sequences import ajustar_secuencias
from tools.etl_puntorojo.transform import transformar
from tools.provision_tenant import _db_name

log = get_logger("etl_puntorojo")


def _exportar_pdfs_cuentas_cobro(origen: dict, destino_dir: Path) -> None:
    """El destino no guarda `pdf_bytes`: los PDFs de cuentas de cobro se exportan a archivo."""
    con_pdf = [r for r in origen.get("cuentas_cobro", []) if r.get("pdf_bytes")]
    if not con_pdf:
        return
    destino_dir.mkdir(parents=True, exist_ok=True)
    for r in con_pdf:
        ruta = destino_dir / f"cuenta_cobro_{r['id']}_{r.get('numero_display', '')}.pdf"
        ruta.write_bytes(bytes(r["pdf_bytes"]))
        log.info("PDF de cuenta de cobro %s exportado a %s", r["id"], ruta)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="ETL FerreBot → tenant Punto Rojo")
    parser.add_argument("--origen-url", required=True)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--slug")
    grupo.add_argument("--tenant-url")
    parser.add_argument("--limpiar", action="store_true",
                        help="TRUNCATE de las tablas del ETL antes de cargar")
    parser.add_argument("--dry-run", action="store_true", help="no escribe en el destino")
    args = parser.parse_args(argv)

    url = args.tenant_url or tenant_url(get_settings().tenants_direct_url_base, _db_name(args.slug))

    origen = leer_origen(args.origen_url)
    datos = transformar(origen)
    log.info("transformadas %d tablas destino", len(datos))

    if args.dry_run:
        for tabla, filas in datos.items():
            print(f"{tabla:32s} {len(filas):5d} filas")
        print("\n(dry-run: no se escribió nada)")
        return 0

    _exportar_pdfs_cuentas_cobro(origen, Path("backups") / "etl_puntorojo")
    reportes = cargar(url, datos, limpiar=args.limpiar)
    ajustar_secuencias(url)

    print(f"\n{'tabla':32s} {'leídas':>7s} {'insertadas':>10s} {'saltadas':>9s}")
    for tabla, r in reportes.items():
        print(f"{tabla:32s} {r.leidas:7d} {r.insertadas:10d} {r.saltadas:9d}")
    print("\nCarga completa. Corre la paridad: python -m tools.etl_puntorojo.verify "
          f"--origen-url ... {'--slug ' + args.slug if args.slug else '--tenant-url ...'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
