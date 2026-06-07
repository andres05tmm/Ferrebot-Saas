"""Adaptador de catálogo para el bypass del bot — capa EXACTA.

Ubicación (decisión): vive en `apps/bot` (composition root del bot), NO en `ai/`. Es el punto donde
se ensambla el Bypass y donde los repositorios del tenant se conectan a la herramienta de match
rápido; mantener aquí el adaptador concreto deja `ai/` libre de dependencias del repo y agrupa el
cableado junto al resto de los adaptadores del bot (`apps/bot/wiring.py`).

Implementa `ai.bypass.CatalogoBypass` sobre la capa exacta del repo de inventario
(`buscar_exacta`) + el read que ya arma el `EsquemaPrecio` (`SqlVentasRepository.obtener_producto`):
resuelve un slug a producto SOLO si hay UN único match exacto (0 o >1 → None; el bypass no adivina).
"""
from __future__ import annotations

from ai.bypass import ProductoBypass
from modules.inventario.repository import SqlInventarioRepository
from modules.ventas.repository import SqlVentasRepository


class CatalogoBypassExacto:
    """Adaptador `CatalogoBypass` sobre la capa exacta de inventario + el esquema de precio de ventas."""

    def __init__(self, inventario: SqlInventarioRepository, ventas: SqlVentasRepository) -> None:
        self._inventario = inventario   # capa EXACTA: buscar_exacta(query, limite)
        self._ventas = ventas           # obtener_producto → ProductoPrecio (arma el EsquemaPrecio)

    async def producto_exacto(self, slug: str) -> ProductoBypass | None:
        """Único match exacto del slug (con su `EsquemaPrecio`), o None (0 o >1 → no adivina).

        `buscar_exacta` compara lower(btrim(nombre)) == lower(btrim(slug)); el slug del bypass ya
        viene normalizado (minúsculas, sin tildes), así que casa con los nombres simples del catálogo.
        Un nombre con tildes que no case aquí cae al modelo (no es pérdida: el modelo busca completo).
        """
        matches = await self._inventario.buscar_exacta(slug, 2)
        if len(matches) != 1:
            return None
        prod = await self._ventas.obtener_producto(matches[0][0])
        if prod is None:
            return None
        return ProductoBypass(id=prod.id, nombre=prod.nombre, esquema=prod.esquema())
