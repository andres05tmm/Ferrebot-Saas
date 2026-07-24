"""Punto de entrada del agente:  python -m tools.agente_impresion [ruta/config.json]

Empaquetado Windows: ver docs/agente-impresion.md (PyInstaller). El registro local y el log
viven junto al config.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx

from tools.agente_impresion.agente import AgenteImpresion, RegistroImpresos
from tools.agente_impresion.config import cargar_config


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    ruta_config = Path(args[0]) if args else Path("config.json")
    if not ruta_config.exists():
        print(f"ERROR: no existe {ruta_config}. Copia config.example.json y edítalo.", file=sys.stderr)
        return 1
    base = ruta_config.parent
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(base / "agente_impresion.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    config = cargar_config(ruta_config)
    agente = AgenteImpresion(
        config,
        RegistroImpresos(base / "impresos.txt"),
        http=httpx.Client(timeout=15),
    )
    logging.getLogger("agente_impresion").info(
        "agente arriba: %s (%s), %d impresora(s)", config.url, config.slug, len(config.impresoras)
    )
    agente.correr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
