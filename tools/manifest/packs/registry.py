"""Registro declarativo de packs (ADR 0007 §D4): el *seam* que el panel super-admin futuro togglea.

Cada pack se declara UNA vez (flag del catálogo → loader idempotente + tablas que toca). El
provisionador (fase 3) itera solo los packs ACTIVOS según las features efectivas y corre su loader.
Añadir un vertical nuevo = registrar un `Pack` + su loader, sin tocar el orquestador.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tools.manifest.packs.agenda import cargar_agenda
from tools.manifest.packs.faq import cargar_faq


@dataclass(frozen=True, slots=True)
class Pack:
    flag: str                                          # feature del catálogo (core/tenancy/catalogo.py)
    # (manifiesto.packs.<nombre>, conn) -> conteos. `None` = pack ESTRUCTURAL: sus tablas las crea la
    # migración del esquema y no tiene datos declarativos por sembrar (p. ej. `pos`).
    loader: Callable[..., dict[str, int]] | None
    tablas: tuple[str, ...]                             # tablas del pack (para el smoke de verificación)


PACKS: dict[str, Pack] = {
    # `pos` (ADR 0008): pack grueso de retail. Sin loader: las tablas POS ya existen en toda app DB
    # (principio de feature-flags.md, vacías si no se usan); no hay datos de pack que sembrar.
    "pos": Pack(
        flag="pos",
        loader=None,
        tablas=("ventas", "inventario", "caja", "gastos", "compras", "proveedores", "productos"),
    ),
    "pack_agenda": Pack(
        flag="pack_agenda",
        loader=cargar_agenda,
        tablas=("servicios", "recursos", "recurso_servicio", "disponibilidad", "agenda_config"),
    ),
    "pack_faq": Pack(
        flag="pack_faq",
        loader=cargar_faq,
        tablas=("conocimiento",),
    ),
}


def packs_activos(efectivas: frozenset[str]) -> list[Pack]:
    """Los packs cuyo flag está en el set EFECTIVO de capacidades, en orden de declaración."""
    return [pack for flag, pack in PACKS.items() if flag in efectivas]
