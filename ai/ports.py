"""Puertos del despachador: catálogo de precios (para los rieles) y umbrales por empresa.

Los rieles son puros; el IO que necesitan entra por estos puertos, que el despachador resuelve
del tenant. Capacidades NO está aquí: viajan ya resueltas en `Contexto.capacidades`
(feature-flags.md), el despachador las lee directo. Los umbrales (la costura del ADR 0005) salen
de `config_empresa`; aquí van el puerto + el store real y los defaults de plataforma.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from modules.inventario.precios import EsquemaPrecio

# Claves en config_empresa (texto plano, no secreto).
_CLAVE_CONFIRMAR = "confirmar_mutaciones"
_CLAVE_TOL_PCT = "precio_tolerancia_pct"
_CLAVE_TOL_MIN = "precio_tolerancia_min"


# --- Catálogo de precios (para R1/R2) ----------------------------------------
@dataclass(frozen=True, slots=True)
class ProductoCatalogo:
    """Lo que un riel necesita de un producto: identidad, si está activo y su esquema de precio."""

    id: int
    nombre: str
    activo: bool
    esquema: EsquemaPrecio


class CatalogoPrecios(Protocol):
    """Resuelve un `producto_id` del tenant a su info de precio (o None si no existe)."""

    async def obtener(self, producto_id: int) -> ProductoCatalogo | None: ...


class CatalogoDesdeVentas:
    """Adaptador real: reusa `obtener_producto` del repo de ventas (misma fuente de verdad)."""

    def __init__(self, repo) -> None:  # repo con obtener_producto -> ProductoPrecio | None
        self._repo = repo

    async def obtener(self, producto_id: int) -> ProductoCatalogo | None:
        prod = await self._repo.obtener_producto(producto_id)
        if prod is None:
            return None
        return ProductoCatalogo(
            id=prod.id, nombre=prod.nombre, activo=prod.activo, esquema=prod.esquema()
        )


# --- Umbrales por empresa (config_empresa) -----------------------------------
@dataclass(frozen=True, slots=True)
class Umbrales:
    """Umbrales de los rieles. Defaults seguros (confirmar ON, tolerancia 1 % / mín 1 peso)."""

    confirmar_mutaciones: bool = True
    precio_tolerancia_pct: Decimal = Decimal("1")
    precio_tolerancia_min: Decimal = Decimal("1")


DEFECTO = Umbrales()


class UmbralesStore(Protocol):
    async def cargar(self, empresa_id: int) -> Umbrales: ...


def _a_bool(valor: str | None, defecto: bool) -> bool:
    if valor is None:
        return defecto
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _a_decimal(valor: str | None, defecto: Decimal) -> Decimal:
    if valor is None:
        return defecto
    try:
        return Decimal(valor)
    except (InvalidOperation, ValueError):
        return defecto


def umbrales_desde_overrides(overrides: dict[str, str]) -> Umbrales:
    """Arma `Umbrales` desde el dict clave→valor de config_empresa, con defaults seguros."""
    return Umbrales(
        confirmar_mutaciones=_a_bool(overrides.get(_CLAVE_CONFIRMAR), DEFECTO.confirmar_mutaciones),
        precio_tolerancia_pct=_a_decimal(overrides.get(_CLAVE_TOL_PCT), DEFECTO.precio_tolerancia_pct),
        precio_tolerancia_min=_a_decimal(overrides.get(_CLAVE_TOL_MIN), DEFECTO.precio_tolerancia_min),
    )


class ControlUmbralesStore:
    """Store real: lee los umbrales de `config_empresa` (mismo plano que el config del LLM)."""

    def __init__(self, config_store) -> None:  # config_store con overrides(empresa_id) -> dict
        self._config = config_store

    async def cargar(self, empresa_id: int) -> Umbrales:
        return umbrales_desde_overrides(await self._config.overrides(empresa_id))
