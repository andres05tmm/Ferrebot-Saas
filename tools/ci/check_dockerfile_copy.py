"""Lint de CI: todo paquete Python top-level debe tener su COPY en el Dockerfile.

La imagen de prod (api·bot·worker) copia los paquetes uno a uno (`COPY core/ ./core/`...).
CI corre con el repo completo en PYTHONPATH, así que un paquete nuevo sin COPY pasa la suite
en verde y crashea en prod con ModuleNotFoundError (pasó con `services/` tras el PR #77,
fix e8b9492). Este check hace el invariante mecánico.

Sin dependencias: corre con el python3 del runner.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# Paquetes top-level que NO viajan a la imagen a propósito.
EXCLUIDOS = {"tests"}


def paquetes_top_level() -> set[str]:
    """Directorios de primer nivel con __init__.py directo (importables en runtime)."""
    return {
        d.name
        for d in RAIZ.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and d.name not in EXCLUIDOS
        and (d / "__init__.py").is_file()
    }


def paquetes_copiados(dockerfile: Path) -> set[str]:
    """Nombres con línea `COPY <dir>/ ./<dir>/` en el Dockerfile."""
    patron = re.compile(r"^COPY\s+([A-Za-z0-9_]+)/\s+\./\1/\s*$", re.MULTILINE)
    return set(patron.findall(dockerfile.read_text(encoding="utf-8")))


def main() -> int:
    dockerfile = RAIZ / "Dockerfile"
    if not dockerfile.is_file():
        print(f"ERROR: no existe {dockerfile}")
        return 1

    paquetes = paquetes_top_level()
    copiados = paquetes_copiados(dockerfile)
    faltantes = sorted(paquetes - copiados)

    if faltantes:
        print("FALLO: paquetes Python top-level SIN COPY en el Dockerfile (crash de prod garantizado):")
        for p in faltantes:
            print(f"  - {p}/   →  falta:  COPY {p}/ ./{p}/")
        print("Agrega la línea COPY al Dockerfile o, si el paquete no debe ir a la imagen,")
        print("añádelo a EXCLUIDOS en tools/ci/check_dockerfile_copy.py con una justificación.")
        return 1

    print(f"OK: {len(paquetes)} paquetes top-level con COPY: {', '.join(sorted(paquetes))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
