"""Cargar un manifiesto de tenant desde disco (ADR 0007 §D1).

`yaml.safe_load` parsea YAML **y** JSON (JSON es subconjunto de YAML) → un solo loader, retrocompatible
con los `tools/onboarding/*.json` actuales. NO toca la base de datos: solo lee y tipa (Pydantic).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tools.manifest.schema import Manifiesto


def cargar_manifiesto(path: str | Path) -> Manifiesto:
    """Lee un manifiesto (YAML o JSON) y lo tipa como `Manifiesto`.

    Lanza `FileNotFoundError` si no existe, `yaml.YAMLError` si el texto no parsea y
    `pydantic.ValidationError` si la forma/tipos no encajan con el esquema.
    """
    ruta = Path(path)
    texto = ruta.read_text(encoding="utf-8")
    datos = yaml.safe_load(texto)
    if not isinstance(datos, dict):
        raise ValueError(f"manifiesto vacío o mal formado: {ruta} (se esperaba un mapa en la raíz)")
    return Manifiesto.model_validate(datos)
