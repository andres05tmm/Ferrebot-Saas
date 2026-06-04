"""Registry de proveedores LLM: agregar un proveedor es una línea (nombre → clase)."""
import pytest

from core.llm import registry
from core.llm.base import ProveedorDesconocido


def test_proveedores_base_registrados():
    nombres = registry.proveedores()
    assert "claude" in nombres
    assert "openai" in nombres


def test_obtener_clase_desconocida_falla():
    with pytest.raises(ProveedorDesconocido):
        registry.obtener_clase("inexistente")


def test_registrar_proveedor_nuevo():
    class _Falso:
        nombre = "falso"

        def __init__(self, *, api_key, client=None):
            self.api_key = api_key

    registry.registrar("falso", _Falso)
    try:
        assert registry.obtener_clase("falso") is _Falso
    finally:
        registry._PROVIDERS.pop("falso", None)
