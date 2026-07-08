"""AIU — totales de una cotización de obra (plan PIM §4, skill money-safe "AIU formula").

AIU = Administración + Imprevistos + Utilidad, el modelo de cotización de obra pública en
Colombia. Regla de negocio crítica del cliente: **el IVA (19%) grava SOLO la utilidad**, no
el subtotal ni la administración ni los imprevistos. Con márgenes de 3–4%, aplicar IVA sobre
la base equivocada descuadra la rentabilidad, por eso esta es la única fuente de verdad.

Fórmula canónica (skill money-safe):
    subtotal       = Σ(cantidad × valor_unitario)
    administracion = subtotal × administracion_pct
    imprevistos    = subtotal × imprevistos_pct
    utilidad       = subtotal × utilidad_pct
    iva_utilidad   = utilidad × iva_sobre_utilidad_pct   # IVA SOLO sobre la utilidad
    total          = subtotal + administracion + imprevistos + utilidad + iva_utilidad

Los `*_pct` son fracciones (0.05 = 5%), tal como se guardan en `cotizaciones_obra`
(spec 01_MODELO_DATOS: `administracionPct Decimal @default(0)`, `ivaSobreUtilidadPct 0.19`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Protocol

from core.money import cuantizar


class LineaAIU(Protocol):
    """Contrato mínimo de un ítem de cotización para el cálculo (duck typing).

    El caller real pasa `ItemCotizacionObra` (ORM, Fase 1); los tests pasan cualquier
    objeto con estos dos atributos. La función pura no depende del modelo concreto.
    """

    cantidad: Decimal
    valor_unitario: Decimal


@dataclass(frozen=True, slots=True)
class TotalesAIU:
    """Desglose inmutable del total AIU. Todos los campos ya cuantizados (salida)."""

    subtotal: Decimal
    administracion: Decimal
    imprevistos: Decimal
    utilidad: Decimal
    iva_utilidad: Decimal
    total: Decimal


def calcular_totales_cotizacion(
    items: Iterable[LineaAIU],
    administracion_pct: Decimal,
    imprevistos_pct: Decimal,
    utilidad_pct: Decimal,
    iva_sobre_utilidad_pct: Decimal,
) -> TotalesAIU:
    """Totales AIU de una cotización. IVA solo sobre la utilidad.

    Redondeo SOLO al final: el subtotal y cada componente se calculan con precisión plena
    (`Decimal` sin cuantizar) y solo se cuantizan al construir el resultado, para no arrastrar
    error de centavo entre pasos (skill money-safe: "Round only at the end").

    `items` vacío → subtotal 0 y todos los componentes 0 (cotización sin ítems).
    """
    subtotal = sum(
        (linea.cantidad * linea.valor_unitario for linea in items),
        start=Decimal("0"),
    )
    administracion = subtotal * administracion_pct
    imprevistos = subtotal * imprevistos_pct
    utilidad = subtotal * utilidad_pct
    iva_utilidad = utilidad * iva_sobre_utilidad_pct
    total = subtotal + administracion + imprevistos + utilidad + iva_utilidad

    return TotalesAIU(
        subtotal=cuantizar(subtotal),
        administracion=cuantizar(administracion),
        imprevistos=cuantizar(imprevistos),
        utilidad=cuantizar(utilidad),
        iva_utilidad=cuantizar(iva_utilidad),
        total=cuantizar(total),
    )
