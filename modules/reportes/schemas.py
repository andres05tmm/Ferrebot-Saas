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


class FlujoDinero(BaseModel):
    """Flujo de dinero simple del rango (sin exigir el ledger contable): qué entró y qué salió.

    El fiado NO es entrada (es cartera): viaja aparte como `ventas_fiado` informativo; los abonos
    de fiados sí entran. Los abonos a proveedor generados por un gasto ya cuentan en gastos
    (dedup ADR 0028)."""

    desde: date
    hasta: date
    ventas_por_metodo: dict[str, Decimal]
    ventas_fiado: Decimal
    abonos_fiados: Decimal
    ingresos_caja: Decimal
    total_entradas: Decimal
    gastos_por_categoria: dict[str, Decimal]
    abonos_proveedores: Decimal
    egresos_caja: Decimal
    total_salidas: Decimal
    neto: Decimal


class MargenProducto(BaseModel):
    """Margen bruto por producto (o categoría) del rango, con cobertura de costo honesta."""

    clave: str
    producto_id: int | None
    cantidad: Decimal
    ingresos: Decimal
    cogs: Decimal
    margen: Decimal
    margen_pct: Decimal | None      # None si no hay ingresos
    cobertura_pct: Decimal          # % de unidades CON costo snapshot (100 = margen confiable)


class AgingProveedor(BaseModel):
    """Deuda a un proveedor por tramos de antigüedad (días desde la factura)."""

    proveedor: str
    total_pendiente: Decimal
    d0_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_mas: Decimal
    facturas: int
    mas_vieja_dias: int
    semaforo: str                   # verde (≤30) | ambar (≤60) | rojo (>60)


class ProyeccionCaja(BaseModel):
    """Proyección del cierre del mes con el promedio de los últimos 14 días CON movimiento."""

    dias_restantes: int
    promedio_venta_diaria: Decimal
    promedio_gasto_diario: Decimal
    ventas_mes_actual: Decimal
    gastos_mes_actual: Decimal
    proyeccion_ventas_mes: Decimal
    proyeccion_gastos_mes: Decimal
    proyeccion_neto_mes: Decimal


class DiaCalendarioLeer(BaseModel):
    """Un día del calendario mensual (heatmap): ventas, transacciones y gastos."""

    fecha: date
    total: Decimal
    num_ventas: int
    gastos: Decimal


class HoyDashboard(BaseModel):
    """Agregado del cockpit /hoy (reforma F4): las señales que NO salen de los endpoints del día ya
    existentes — utilidad estimada (solo admin: None para vendedor), alertas de pedidos a proveedor,
    vencimientos de CxP, cartera de fiados y el avance del inventario progresivo."""

    fecha: date
    caja_abierta: bool
    ingresos_hoy: Decimal
    gastos_hoy: Decimal
    utilidad_estimada: Decimal | None = None   # ventas − costo − gastos del día; solo admin
    pedidos_en_camino: int
    pedidos_demorados: int
    pedido_mas_viejo_horas: float | None
    cxp_vencidas: int
    cxp_monto_vencido: Decimal
    cxp_por_vencer_7d: int
    cxp_monto_por_vencer: Decimal
    fiados_total: Decimal
    productos_activos: int
    productos_cuadrados: int
    stock_bajo_confiables: int
