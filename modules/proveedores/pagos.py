"""Partes de un pago a proveedor (pago MIXTO, 0068): lógica PURA, sin SQL ni red.

Un pago —al pedir, al recibir o al abonar— puede repartirse entre medios: una parte del cajón, otra
por transferencia. Aquí se normaliza y se valida esa repartición; el llamador solo tiene que saber
cuánto va por caja (lo único que mueve el arqueo).
"""
from decimal import Decimal

from pydantic import BaseModel, Field

from core.money import cuantizar
from modules.caja.schemas import OrigenFondos

ORIGEN_MIXTO = "mixto"


class PartePago(BaseModel):
    """Una parte del pago: de dónde sale y cuánto."""

    origen: OrigenFondos
    monto: Decimal = Field(gt=0)


class PagoInvalido(ValueError):
    """La repartición no cuadra con el monto a pagar, o repite un medio."""


def normalizar_partes(
    partes: list[PartePago] | None, origen_fondos: str, monto: Decimal
) -> list[PartePago]:
    """Partes efectivas del pago.

    Sin repartición explícita, el pago va completo por `origen_fondos` (el caso normal). Con
    repartición: los montos deben sumar EXACTAMENTE el monto a pagar y no se repite medio (dos
    partes del mismo bolsillo son una sola). Devuelve [] si no hay nada que pagar.
    """
    monto = cuantizar(monto)
    if monto <= 0:
        return []
    if not partes:
        return [PartePago(origen=origen_fondos, monto=monto)]

    origenes = [p.origen for p in partes]
    if len(set(origenes)) != len(origenes):
        raise PagoInvalido("El pago mixto no puede repetir el mismo medio: súmalos en una parte")
    suma = cuantizar(sum((p.monto for p in partes), Decimal("0")))
    if suma != monto:
        raise PagoInvalido(
            f"Las partes del pago suman {suma} pero hay que pagar {monto}"
        )
    return [PartePago(origen=p.origen, monto=cuantizar(p.monto)) for p in partes]


def monto_de_caja(partes: list[PartePago]) -> Decimal:
    """Lo que sale del cajón físico: lo único que postea movimiento de caja."""
    return cuantizar(sum((p.monto for p in partes if p.origen == "caja"), Decimal("0")))


def etiqueta_origen(partes: list[PartePago]) -> str | None:
    """Cómo se pagó, para el badge/columna de siempre: el medio único o `mixto`."""
    if not partes:
        return None
    if len(partes) == 1:
        return partes[0].origen
    return ORIGEN_MIXTO
