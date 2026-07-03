"""Contratos Pydantic de reportes (salida del API)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ResumenDia(BaseModel):
    """KPIs del día para la pestaña Hoy del dashboard (api-contract.md / B4)."""

    fecha: date
    num_ventas: int
    total_vendido: Decimal
    ticket_promedio: Decimal
    por_metodo_pago: dict[str, Decimal]


class EstadoResultados(BaseModel):
    """Estado de resultados (P&L) de un rango para la pestaña Resultados (Fase 12, Slice 2)."""

    desde: date
    hasta: date
    ingresos: Decimal           # ventas sin IVA (el IVA es traslado)
    costo_ventas: Decimal       # costo de la mercancía vendida (exacto desde el threading por venta)
    utilidad_bruta: Decimal     # ingresos − costo_ventas
    gastos: Decimal
    utilidad_neta: Decimal      # utilidad_bruta − gastos


class LibroIVA(BaseModel):
    """Libro IVA de un rango: cruza el IVA generado (ventas) con el descontable (compras fiscales).

    `saldo = iva_generado − iva_descontable`: positivo = IVA a pagar; negativo = saldo a favor. Es un
    reporte de soporte tributario (Fase 12, Slice 5); no emite ni consulta a la DIAN.
    """

    desde: date
    hasta: date
    base_ventas: Decimal        # base gravable de las ventas (Σ subtotal de no anuladas)
    iva_generado: Decimal       # IVA cobrado en ventas (Σ impuestos de no anuladas)
    base_compras: Decimal       # base de las compras fiscales del rango
    iva_descontable: Decimal    # IVA descontable de compras fiscales del rango
    saldo: Decimal              # iva_generado − iva_descontable (+ = a pagar; − = a favor)


class SaldoBimestral(BaseModel):
    """Saldo de IVA consolidado de un bimestre (materializado, ADR 0027).

    `saldo = iva_generado − iva_descontable` (+ = a pagar; − = a favor). A diferencia de `LibroIVA`
    (cruce al vuelo de un rango arbitrario), esto es el saldo PERSISTIDO por período bimestral.
    """

    anio: int
    bimestre: int
    iva_generado: Decimal
    iva_descontable: Decimal
    saldo: Decimal


class PuntoSerie(BaseModel):
    """Un día de la serie de ventas (para la gráfica de evolución y el sparkline del tab Hoy)."""

    fecha: date
    total: Decimal


class TotalesVentas(BaseModel):
    """Totales de ventas completadas: hoy / últimos 7 días / mes en curso (hora Colombia)."""

    dia: Decimal
    semana: Decimal
    mes: Decimal


class CuentaMayor(BaseModel):
    """Un renglón del Libro Mayor: total por cuenta/concepto en el período (ADR 0027, sin PUC formal).

    `naturaleza` agrupa el concepto (ingreso/egreso/impuesto/retencion) mientras no exista el PUC (F8).
    """

    concepto: str
    naturaleza: str
    total: Decimal


class MovimientoAuxiliar(BaseModel):
    """Un movimiento del Libro Auxiliar: el detalle documento a documento detrás del Mayor (ADR 0027)."""

    fecha: date
    concepto: str
    naturaleza: str
    referencia: str
    valor: Decimal


class TopProducto(BaseModel):
    """Una fila del ranking de productos por cantidad e ingreso en un rango."""

    producto_id: int
    nombre: str
    cantidad: Decimal
    ingreso: Decimal
