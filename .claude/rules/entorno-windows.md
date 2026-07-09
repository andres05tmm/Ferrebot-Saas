# Entorno Windows (dev local)

Gotchas de esta máquina que costaron sesiones enteras. Leer antes de pelear con la terminal.

## Python

- **Usar siempre el venv del repo:** `.venv/Scripts/python.exe` (Python 3.12, gestionado con `uv`).
  `python` a secas NO resuelve (los stubs de Microsoft Store fueron eliminados a propósito; el launcher `py` sí existe).
- Instalar dependencias: `uv sync --extra dev` (pytest vive en el extra `dev`).
- Encoding: `PYTHONUTF8=1` y `PYTHONIOENCODING=utf-8` están seteados (usuario + `.claude/settings.json`). Si un script rompe con caracteres tipo `CUÑETE`, verificar que esas variables llegaron al proceso.

## Postgres / Docker

- Postgres local corre en Docker: contenedor `ferrebot-pg`, **puerto 5433** (no 5432). Redis: `ferrebot-redis` en 6379.
- Tras reiniciar el PC: `docker start ferrebot-pg` (Redis levanta solo). Esperar con `pg_isready -h localhost -p 5433`.
- Cliente local: PostgreSQL 18 (`pg_dump 18.4`). Para dumps/restores contra Railway, verificar la versión del servidor antes de asumir compatibilidad de formato.

## pytest

- La suite completa (~784 tests) tarda >10 min: **correrla en background o troceada** (por prefijo de archivo o `-m eval`), nunca en un solo comando foreground con timeout de 2 min.
- Comando canónico: `uv run pytest -ra --timeout=180 --timeout-method=thread`.

## Convenciones de sesión

- **Screenshots y archivos temporales SIEMPRE al scratchpad de la sesión**, nunca a rutas del sistema ni a la raíz del repo ("Acceso denegado" conocido en otras rutas).
- Chrome/Chromium headless en Windows no baja de ~500px de ancho de ventana (gotcha conocido para capturas móviles).
- Commits directos a `main` están bloqueados por hook (`.claude/hooks/block-commit-main.js`); trabajar en ramas `tipo/descripcion`. Escape consciente: `ALLOW_MAIN_COMMIT=1`.
