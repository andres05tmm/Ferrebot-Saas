"""Registro declarativo de packs (ADR 0007 §D4): el *seam* que el panel super-admin futuro togglea.

Cada pack se declara UNA vez (flag del catálogo → loader idempotente + tablas que toca). El
provisionador (fase 3) itera solo los packs ACTIVOS según las features efectivas y corre su loader.
Añadir un vertical nuevo = registrar un `Pack` + su loader, sin tocar el orquestador.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tools.manifest.packs.agenda import cargar_agenda
from tools.manifest.packs.construccion import cargar_construccion
from tools.manifest.packs.faq import cargar_faq
from tools.manifest.packs.pedidos import cargar_pedidos
from tools.manifest.packs.pos import cargar_pos


@dataclass(frozen=True, slots=True)
class Pack:
    flag: str                                          # feature del catálogo (core/tenancy/catalogo.py)
    # (manifiesto.packs.<nombre>, conn) -> conteos. `None` = pack ESTRUCTURAL: sus tablas las crea la
    # migración del esquema y no tiene datos declarativos por sembrar (p. ej. `caja`).
    loader: Callable[..., dict[str, int]] | None
    tablas: tuple[str, ...]                             # tablas del pack (para el smoke de verificación)
    # Sección del manifiesto que alimenta el loader; `None` = convención flag.removeprefix("pack_").
    # Permite que la feature fina `ventas` siga leyendo la sección YAML `packs.pos` (compat ADR 0021).
    seccion: str | None = None


PACKS: dict[str, Pack] = {
    # Partición del retail (ADR 0021): la feature fina `ventas` hereda el loader declarativo del pack
    # `pos` (ADR 0011: catálogo —productos, fracciones, aliases— e inventario de apertura). La sección
    # YAML sigue llamándose `packs.pos` (compat con manifiestos existentes y onboarding-magico). NO
    # existe entrada `pos`: el meta-pack expande a las finas en el set efectivo, así el loader corre
    # UNA vez. Si el manifiesto declara `stock_inicial` sin la feature `inventario`, la siembra es
    # inofensiva (la tabla existe en todo tenant; ver feature-flags.md).
    "ventas": Pack(
        flag="ventas",
        loader=cargar_pos,
        seccion="pos",
        tablas=("ventas", "productos", "productos_fracciones", "aliases"),
    ),
    # Packs ESTRUCTURALES de la partición: sin datos declarativos propios; tablas para el smoke.
    "caja": Pack(
        flag="caja",
        loader=None,
        tablas=("caja", "caja_movimientos", "gastos"),
    ),
    "inventario": Pack(
        flag="inventario",
        loader=None,
        tablas=("inventario", "movimientos_inventario", "compras", "proveedores"),
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
    # `pack_pedidos` (ADR 0016): el MENÚ es el catálogo del POS (`packs.pos`, su dependencia); este
    # loader solo siembra lo OPERATIVO declarativo: `pedido_config` (una fila) + `zonas_domicilio`.
    "pack_pedidos": Pack(
        flag="pack_pedidos",
        loader=cargar_pedidos,
        tablas=("pedido_config", "zonas_domicilio", "pedidos", "pedido_items"),
    ),
    # `pack_reservas` (plan §2.7): la variante NOCHES del motor de agenda (su dependencia `pack_agenda`).
    # NO tiene datos declarativos propios — sus recursos (habitaciones), servicios y `agenda_config`
    # (incl. checkin_hora/checkout_hora) se declaran bajo `packs.agenda` y los siembra `cargar_agenda`.
    # loader=None (pack ESTRUCTURAL sobre agenda): el provisionador lo salta sin buscar sección propia.
    "pack_reservas": Pack(
        flag="pack_reservas",
        loader=None,
        tablas=("citas", "recursos", "agenda_config"),
    ),
    # Vertical CONSTRUCCIÓN (plan §8): igual que `ventas` respecto a `pos`, el pack se registra bajo UNA
    # fina del meta-pack —`obras`, el corazón del vertical— para que su loader corra UNA sola vez aunque
    # `construccion` expanda a siete finas. La sección del manifiesto es `packs.construccion` (explícita,
    # no la convención flag→sección, porque el flag disparador es `obras`). El loader siembra
    # `parametros_legales` 2026 + catálogos default; las cuatro tablas las crea la migración de tenant 0043.
    "obras": Pack(
        flag="obras",
        loader=cargar_construccion,
        seccion="construccion",
        tablas=("parametros_legales", "maquinas", "herramientas", "trabajadores"),
    ),
}


def packs_activos(efectivas: frozenset[str]) -> list[Pack]:
    """Los packs cuyo flag está en el set EFECTIVO de capacidades, en orden de declaración."""
    return [pack for flag, pack in PACKS.items() if flag in efectivas]
