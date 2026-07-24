"""Entry point para PyInstaller (no empaqueta `__main__.py` de un paquete directamente).

Build: ver docs/agente-impresion.md §4.
"""
import sys

from tools.agente_impresion.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
