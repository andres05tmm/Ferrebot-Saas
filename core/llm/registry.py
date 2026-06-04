"""Registry de proveedores: nombre → clase. Agregar un proveedor = una línea en `_PROVIDERS`.

El despachador resuelve la clase por nombre (lo que vino de config_empresa o del .env), nunca
importa un provider concreto.
"""
from core.llm.base import LLMProvider, ProveedorDesconocido
from core.llm.providers.claude import ClaudeProvider
from core.llm.providers.openai import OpenAIProvider

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
}


def registrar(nombre: str, clase: type[LLMProvider]) -> None:
    """Agrega o reemplaza un proveedor en el registry."""
    _PROVIDERS[nombre] = clase


def obtener_clase(nombre: str) -> type[LLMProvider]:
    try:
        return _PROVIDERS[nombre]
    except KeyError as exc:
        raise ProveedorDesconocido(nombre) from exc


def proveedores() -> tuple[str, ...]:
    return tuple(_PROVIDERS)
