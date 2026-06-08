"""Manifiesto de tenant (ADR 0007): parsing, validación y (futuro) provisionado.

Fase 1 — SOLO parsing y validación; NO toca la base de datos. Falla cerrado: si algo no valida,
no se escribe nada (la validación corre antes de cualquier IO, igual que `cargar_plan_features`).

- `schema.py`     tipa el manifiesto (Pydantic v2), 1:1 con el ejemplo `*.example.yaml`.
- `loader.py`     `cargar_manifiesto(path)` -> Manifiesto (YAML o JSON, vía `yaml.safe_load`).
- `validacion.py` `validar(manifiesto)` reusa `core/tenancy/catalogo` y comprueba packs.
"""
from tools.manifest.loader import cargar_manifiesto
from tools.manifest.schema import Manifiesto
from tools.manifest.validacion import ErrorManifiesto, validar

__all__ = ["Manifiesto", "cargar_manifiesto", "validar", "ErrorManifiesto"]
