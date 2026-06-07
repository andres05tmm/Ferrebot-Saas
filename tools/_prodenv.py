"""Cargar `.env.prod` para correr tools de OPERACIÓN contra PRODUCCIÓN (no el `.env` local).

La trampa del localhost: el `.env` del repo apunta a Postgres local (Docker en :5433). Un tool de
respaldo/restauración debe pegarle a las URLs PÚBLICAS de Railway. Este helper vuelca `.env.prod` a
`os.environ` (que tiene prioridad sobre el `.env` en pydantic-settings) y limpia el caché de
`get_settings()` para que `Settings` se relea desde prod. Llamar UNA vez al inicio del tool.

`.env.prod` NO se commitea (lleva password + master key); ver `.env.prod.example`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from core.config import get_settings

_PROD_ENV = Path(".env.prod")


def cargar_env_prod(ruta: Path | str = _PROD_ENV) -> Path:
    """Carga `ruta` (default `.env.prod`) a `os.environ` y limpia el caché de settings. Devuelve la ruta.

    Lanza `FileNotFoundError` con instrucciones si no existe (es un descuido fácil de cometer)."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(
            f"No existe {ruta}. Copia .env.prod.example a {ruta} con las URLs públicas de Railway "
            "(NO se commitea: lleva password + SECRETS_MASTER_KEY)."
        )
    for clave, valor in parsear_env(ruta.read_text(encoding="utf-8")).items():
        os.environ[clave] = valor
    get_settings.cache_clear()
    return ruta


def parsear_env(texto: str) -> dict[str, str]:
    """Parser mínimo de `.env`: `CLAVE=VALOR` por línea. Ignora líneas vacías y comentarios (`#`).
    Quita comillas envolventes y comentarios al final de línea (` #...`). PURO (testeable)."""
    datos: dict[str, str] = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        if clave:
            datos[clave] = _limpiar_valor(valor)
    return datos


def _limpiar_valor(valor: str) -> str:
    """Normaliza el lado derecho de `CLAVE=VALOR`:
    - valor entre comillas → su contenido literal (se respeta cualquier `#` interno);
    - sin comillas → se recorta un comentario inline (` #...`, espacio + almohadilla) y los espacios.
      Un `#` PEGADO al valor (p. ej. dentro de un password) NO se trata como comentario."""
    valor = valor.strip()
    if valor[:1] in ("'", '"'):
        comilla = valor[0]
        fin = valor.find(comilla, 1)
        return valor[1:fin] if fin != -1 else valor[1:]
    m = re.search(r"\s#", valor)
    if m:
        valor = valor[: m.start()]
    return valor.strip()
