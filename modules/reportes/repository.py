"""Repositorio de reportes: único lugar con SQL (regla no negociable #2).

Agregación del día sobre `ventas`, EXCLUYENDO anuladas (solo `completada`), opcionalmente acotada a
un vendedor. Devuelve el agregado crudo (conteo, total y desglose por método de pago); el servicio
calcula derivados (ticket promedio) y arma el contrato de salida.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case as sa_case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.models import CajaMovimiento, Gasto
from modules.compras_fiscal.models import CompraFiscal
from modules.fiados.models import FiadoMovimiento
from modules.inventario.models import MovimientoInventario, Producto
from modules.proveedores.models import AbonoProveedor, FacturaProveedor
from modules.ventas.models import Venta, VentaDetalle


@dataclass(frozen=True, slots=True)
class AgregadoDia:
    """Agregado crudo del día (ya excluidas las anuladas)."""

    num_ventas: int
    total_vendido: Decimal
    por_metodo_pago: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class AgregadoResultados:
    """Insumos crudos del estado de resultados de un rango (el servicio deriva utilidades)."""

    ingresos: Decimal       # suma de subtotal (sin IVA) de ventas NO anuladas
    costo_ventas: Decimal   # Σ SALIDA(costo×cant) − Σ DEVOLUCION(costo×cant), costo NULL = 0
    gastos: Decimal         # suma de gastos del rango


@dataclass(frozen=True, slots=True)
class AgregadoLibroIVA:
    """Insumos crudos del Libro IVA de un rango (el servicio deriva el saldo)."""

    base_ventas: Decimal       # Σ subtotal de ventas NO anuladas (base gravable de las ventas)
    iva_generado: Decimal      # Σ impuestos de ventas NO anuladas (IVA que se cobró)
    base_compras: Decimal      # Σ base de compras fiscales del rango
    iva_descontable: Decimal   # Σ iva de compras fiscales del rango (IVA que se puede descontar)


@dataclass(frozen=True, slots=True)
class TopProductoFila:
    """Una fila del ranking de productos (cantidad e ingreso agregados)."""

    producto_id: int
    nombre: str
    cantidad: Decimal
    ingreso: Decimal


@dataclass(frozen=True, slots=True)
class AgregadoFlujo:
    """Insumos crudos del flujo de dinero de un rango (el servicio deriva totales y neto).

    ENTRADAS: ventas cobradas por método (el fiado NO entra: es cartera, no dinero), abonos de
    fiados (ahí sí entró la plata) e ingresos manuales de caja. SALIDAS: gastos por categoría,
    abonos a proveedores SIN los generados por un gasto (ese dinero ya está en gastos — dedup
    ADR 0028) y egresos manuales de caja (anticipos/pagos de mercancía, retiros)."""

    ventas_por_metodo: dict[str, Decimal]      # sin 'fiado'
    ventas_fiado: Decimal                      # informativo: se vendió a crédito (no es entrada)
    abonos_fiados: Decimal
    ingresos_caja: Decimal
    gastos_por_categoria: dict[str, Decimal]
    abonos_proveedores: Decimal                # solo abonos SIN gasto asociado
    egresos_caja: Decimal                      # egresos manuales (sin los de gastos)


@dataclass(frozen=True, slots=True)
class MargenProductoFila:
    """Margen por producto/categoría del rango: ingresos (sin IVA) vs COGS snapshoteado."""

    clave: str                 # nombre del producto o de la categoría
    producto_id: int | None    # None cuando se agrupa por categoría
    cantidad: Decimal
    ingresos: Decimal
    cogs: Decimal              # Σ SALIDA − DEVOLUCION al costo snapshot; NULL cuenta 0
    unidades_sin_costo: Decimal  # cantidad vendida SIN costo snapshot (cobertura honesta)


@dataclass(frozen=True, slots=True)
class AgingProveedorFila:
    """Cartera por pagar de un proveedor, en tramos por antigüedad de la factura (días)."""

    proveedor: str
    total_pendiente: Decimal
    d0_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_mas: Decimal
    facturas: int
    mas_vieja_dias: int


@dataclass(frozen=True, slots=True)
class DiaCalendario:
    """Agregado de UN día para el calendario mensual (heatmap): ventas, transacciones y gastos."""

    fecha: date
    total: Decimal
    num_ventas: int
    gastos: Decimal


@dataclass(frozen=True, slots=True)
class EstadoPedidosProveedor:
    """Pedidos a proveedor EN CAMINO para las alertas del cockpit /hoy."""

    en_camino: int
    demorados: int          # pasaron la fecha estimada o el promedio histórico del proveedor
    mas_viejo_horas: float | None


@dataclass(frozen=True, slots=True)
class VencimientosCxP:
    """Cuentas por pagar con vencimiento explícito: vencidas y por vencer en 7 días."""

    vencidas: int
    monto_vencido: Decimal
    por_vencer_7d: int
    monto_por_vencer: Decimal


@dataclass(frozen=True, slots=True)
class InventarioConfiable:
    """Avance del inventario progresivo: productos activos vs cuadrados, y stock bajo CONFIABLE."""

    productos_activos: int
    productos_cuadrados: int
    stock_bajo_confiables: int


class SqlReportesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resumen(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> AgregadoDia:
        """Agrupa las ventas completadas del rango por método de pago; suma conteo y total."""
        condiciones = [
            Venta.estado == "completada",
            Venta.fecha >= inicio,
            Venta.fecha <= fin,
        ]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        stmt = (
            select(
                Venta.metodo_pago,
                func.count().label("num"),
                func.coalesce(func.sum(Venta.total), 0).label("total"),
            )
            .where(*condiciones)
            .group_by(Venta.metodo_pago)
        )
        filas = (await self._s.execute(stmt)).all()
        por_metodo = {fila.metodo_pago: Decimal(fila.total) for fila in filas}
        num_ventas = sum(fila.num for fila in filas)
        total_vendido = sum((Decimal(fila.total) for fila in filas), Decimal("0"))
        return AgregadoDia(
            num_ventas=num_ventas, total_vendido=total_vendido, por_metodo_pago=por_metodo
        )

    async def serie_ventas(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> list[tuple[date, Decimal]]:
        """Serie diaria de ventas completadas del rango, agrupada por día en hora Colombia.

        Convierte `fecha` (TIMESTAMPTZ) a la fecha local de Bogotá (`timezone(...)` → `date`) para que
        cada venta caiga en su día Colombia. Excluye anuladas; acota a `vendedor_id` si se da. Devuelve
        solo los días CON ventas (el servicio rellena los vacíos en 0).
        """
        dia = func.date(func.timezone("America/Bogota", Venta.fecha)).label("dia")
        condiciones = [Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        stmt = (
            select(dia, func.coalesce(func.sum(Venta.total), 0).label("total"))
            .where(*condiciones)
            .group_by(dia)
            .order_by(dia)
        )
        filas = (await self._s.execute(stmt)).all()
        return [(f.dia, Decimal(f.total)) for f in filas]

    async def total_ventas(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> Decimal:
        """Suma del total de ventas completadas del rango (acotada al vendedor si se da). 0 si no hay."""
        condiciones = [Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        total = (
            await self._s.execute(
                select(func.coalesce(func.sum(Venta.total), 0)).where(*condiciones)
            )
        ).scalar_one()
        return Decimal(total)

    async def estado_resultados(
        self, *, inicio: datetime, fin: datetime
    ) -> AgregadoResultados:
        """Insumos del P&L del rango: ingresos (sin IVA), costo de ventas exacto y gastos.

        Ingresos = Σ subtotal de ventas completadas (el IVA es traslado, no ingreso). Costo de ventas =
        Σ(costo_unitario × cantidad) de movimientos SALIDA MENOS los DEVOLUCION (ADR 0026: una devolución
        re-ingresa mercancía al costo del snapshot original → revierte su COGS, sin distorsión por el
        promedio del día); un costo NULL (ventas previas al threading) cuenta como 0. Gastos = Σ monto de
        gastos del rango. Es del negocio completo (sin scoping).
        """
        ingresos = (
            await self._s.execute(
                select(func.coalesce(func.sum(Venta.subtotal), 0)).where(
                    Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin,
                )
            )
        ).scalar_one()
        # COGS anclado a la fecha de la venta origen (ADR 0025): `fecha_operacion` (snapshot de la
        # fecha de la venta al crear la SALIDA); cae a `creado_en` para movimientos previos a la 0029.
        fecha_cogs = func.coalesce(
            MovimientoInventario.fecha_operacion, MovimientoInventario.creado_en
        )
        # Signo por tipo: SALIDA suma al COGS, DEVOLUCION lo revierte (contra-COGS al costo snapshot).
        signo = sa_case(
            (MovimientoInventario.tipo == "DEVOLUCION", -1),
            else_=1,
        )
        costo_ventas = (
            await self._s.execute(
                select(
                    func.coalesce(
                        func.sum(
                            signo
                            * MovimientoInventario.cantidad
                            * func.coalesce(MovimientoInventario.costo_unitario, 0)
                        ),
                        0,
                    )
                ).where(
                    MovimientoInventario.tipo.in_(("SALIDA", "DEVOLUCION")),
                    fecha_cogs >= inicio,
                    fecha_cogs <= fin,
                )
            )
        ).scalar_one()
        gastos = (
            await self._s.execute(
                select(func.coalesce(func.sum(Gasto.monto), 0)).where(
                    Gasto.creado_en >= inicio, Gasto.creado_en <= fin,
                )
            )
        ).scalar_one()
        return AgregadoResultados(
            ingresos=Decimal(ingresos), costo_ventas=Decimal(costo_ventas), gastos=Decimal(gastos)
        )

    async def libro_iva(self, *, inicio: datetime, fin: datetime) -> AgregadoLibroIVA:
        """Insumos del Libro IVA del rango: IVA generado (ventas) vs descontable (compras fiscales).

        IVA generado/base de ventas = Σ impuestos/subtotal de ventas completadas (anuladas excluidas);
        el IVA sale de la columna ya calculada al vender (no se recomputa por línea). IVA descontable/
        base de compras = Σ iva/base de `compras_fiscal` del rango (por `creado_en`). Es del negocio
        completo (soporte tributario, sin scoping).
        """
        base_ventas, iva_generado = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(Venta.subtotal), 0),
                    func.coalesce(func.sum(Venta.impuestos), 0),
                ).where(
                    Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin,
                )
            )
        ).one()
        base_compras, iva_descontable = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(CompraFiscal.base), 0),
                    func.coalesce(func.sum(CompraFiscal.iva), 0),
                ).where(
                    CompraFiscal.creado_en >= inicio, CompraFiscal.creado_en <= fin,
                )
            )
        ).one()
        return AgregadoLibroIVA(
            base_ventas=Decimal(base_ventas), iva_generado=Decimal(iva_generado),
            base_compras=Decimal(base_compras), iva_descontable=Decimal(iva_descontable),
        )

    async def flujo_dinero(self, *, inicio: datetime, fin: datetime) -> AgregadoFlujo:
        """Insumos del flujo de dinero del rango. Sin exigir `contabilidad_ledger`: cruza las tablas
        operativas (ventas/fiados/caja/gastos/abonos) — el cashflow simple del negocio familiar.

        Dedup anti-doble-conteo: los egresos de caja EXCLUYEN los posteados por gastos (referencia
        'gasto:%') y los abonos a proveedor EXCLUYEN los generados por un gasto (ADR 0028): ese
        dinero ya está contado en `gastos`.
        """
        filas_ventas = (
            await self._s.execute(
                select(
                    Venta.metodo_pago,
                    func.coalesce(func.sum(Venta.total), 0).label("total"),
                )
                .where(Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin)
                .group_by(Venta.metodo_pago)
            )
        ).all()
        por_metodo = {f.metodo_pago: Decimal(f.total) for f in filas_ventas}
        ventas_fiado = por_metodo.pop("fiado", Decimal("0"))

        abonos_fiados = Decimal(
            (
                await self._s.execute(
                    select(func.coalesce(func.sum(FiadoMovimiento.monto), 0)).where(
                        FiadoMovimiento.tipo == "abono",
                        FiadoMovimiento.creado_en >= inicio,
                        FiadoMovimiento.creado_en <= fin,
                    )
                )
            ).scalar_one()
        )
        # Movimientos manuales de caja: los egresos de GASTO llevan referencia 'gasto:{id}' — se
        # excluyen (ya cuentan en gastos); ingresos/egresos sin esa marca son plata real que entró/salió.
        sin_gasto = (CajaMovimiento.referencia.is_(None)) | (
            ~CajaMovimiento.referencia.like("gasto:%")
        )
        ingresos_caja, egresos_caja = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(
                        sa_case((CajaMovimiento.tipo == "ingreso", CajaMovimiento.monto), else_=0)
                    ), 0),
                    func.coalesce(func.sum(
                        sa_case((CajaMovimiento.tipo == "egreso", CajaMovimiento.monto), else_=0)
                    ), 0),
                ).where(
                    sin_gasto,
                    CajaMovimiento.creado_en >= inicio,
                    CajaMovimiento.creado_en <= fin,
                )
            )
        ).one()

        filas_gastos = (
            await self._s.execute(
                select(
                    Gasto.categoria,
                    func.coalesce(func.sum(Gasto.monto), 0).label("total"),
                )
                .where(Gasto.creado_en >= inicio, Gasto.creado_en <= fin)
                .group_by(Gasto.categoria)
            )
        ).all()

        # Abonos a proveedor SIN gasto asociado (el gasto→CxP ya contó ese dinero en gastos).
        abonos_de_gasto = select(Gasto.abono_proveedor_id).where(
            Gasto.abono_proveedor_id.is_not(None)
        )
        abonos_prov = Decimal(
            (
                await self._s.execute(
                    select(func.coalesce(func.sum(AbonoProveedor.monto), 0)).where(
                        AbonoProveedor.creado_en >= inicio,
                        AbonoProveedor.creado_en <= fin,
                        AbonoProveedor.id.not_in(abonos_de_gasto),
                    )
                )
            ).scalar_one()
        )
        return AgregadoFlujo(
            ventas_por_metodo=por_metodo,
            ventas_fiado=ventas_fiado,
            abonos_fiados=abonos_fiados,
            ingresos_caja=Decimal(ingresos_caja),
            gastos_por_categoria={f.categoria: Decimal(f.total) for f in filas_gastos},
            abonos_proveedores=abonos_prov,
            egresos_caja=Decimal(egresos_caja),
        )

    async def margen_productos(
        self, *, inicio: datetime, fin: datetime, por_categoria: bool, limite: int
    ) -> list[MargenProductoFila]:
        """Margen por producto (o categoría) del rango: ingresos sin IVA vs COGS snapshot.

        Ingresos desde `ventas_detalle` (completadas, sin las varia). COGS y cobertura desde
        `movimientos_inventario` SALIDA/DEVOLUCION anclados a `fecha_operacion` (ADR 0025): un
        costo NULL suma 0 al COGS y sus unidades cuentan como "sin costo" (cobertura honesta —
        lección del viejo dashboard: el CMV en $0 silencioso infla el margen).
        """
        clave = Producto.categoria if por_categoria else Producto.nombre
        # Ingresos por producto/categoría (ventas completadas del rango, sin varia).
        filas_ing = (
            await self._s.execute(
                select(
                    clave.label("clave"),
                    VentaDetalle.producto_id.label("pid"),
                    func.coalesce(func.sum(VentaDetalle.cantidad), 0).label("cantidad"),
                    func.coalesce(
                        func.sum(VentaDetalle.cantidad * VentaDetalle.precio_unitario), 0
                    ).label("ingresos"),
                )
                .join(Venta, Venta.id == VentaDetalle.venta_id)
                .join(Producto, Producto.id == VentaDetalle.producto_id)
                .where(
                    Venta.estado == "completada",
                    Venta.fecha >= inicio,
                    Venta.fecha <= fin,
                    VentaDetalle.producto_id.is_not(None),
                )
                .group_by(clave, VentaDetalle.producto_id)
            )
        ).all()

        # COGS + unidades sin costo por producto (mismo rango, anclado a la fecha de la venta).
        fecha_cogs = func.coalesce(
            MovimientoInventario.fecha_operacion, MovimientoInventario.creado_en
        )
        signo = sa_case((MovimientoInventario.tipo == "DEVOLUCION", -1), else_=1)
        filas_cogs = (
            await self._s.execute(
                select(
                    MovimientoInventario.producto_id.label("pid"),
                    func.coalesce(func.sum(
                        signo
                        * MovimientoInventario.cantidad
                        * func.coalesce(MovimientoInventario.costo_unitario, 0)
                    ), 0).label("cogs"),
                    func.coalesce(func.sum(
                        sa_case(
                            (MovimientoInventario.costo_unitario.is_(None),
                             MovimientoInventario.cantidad),
                            else_=0,
                        )
                    ), 0).label("sin_costo"),
                )
                .where(
                    MovimientoInventario.tipo.in_(("SALIDA", "DEVOLUCION")),
                    fecha_cogs >= inicio,
                    fecha_cogs <= fin,
                )
                .group_by(MovimientoInventario.producto_id)
            )
        ).all()
        cogs_por_pid = {f.pid: (Decimal(f.cogs), Decimal(f.sin_costo)) for f in filas_cogs}

        # Composición en Python (agrupa por clave cuando es categoría) — sin N+1: dos queries.
        acumulado: dict[str, MargenProductoFila] = {}
        for f in filas_ing:
            k = f.clave or "Sin categoría"
            cogs, sin_costo = cogs_por_pid.get(f.pid, (Decimal("0"), Decimal("0")))
            previo = acumulado.get(k)
            if previo is None:
                acumulado[k] = MargenProductoFila(
                    clave=k, producto_id=None if por_categoria else f.pid,
                    cantidad=Decimal(f.cantidad), ingresos=Decimal(f.ingresos),
                    cogs=cogs, unidades_sin_costo=sin_costo,
                )
            else:
                acumulado[k] = MargenProductoFila(
                    clave=k, producto_id=previo.producto_id,
                    cantidad=previo.cantidad + Decimal(f.cantidad),
                    ingresos=previo.ingresos + Decimal(f.ingresos),
                    cogs=previo.cogs + cogs,
                    unidades_sin_costo=previo.unidades_sin_costo + sin_costo,
                )
        filas = sorted(acumulado.values(), key=lambda x: x.ingresos, reverse=True)
        return filas[:limite]

    async def aging_cxp(self, *, hoy: date) -> list[AgingProveedorFila]:
        """Cartera por pagar en tramos de antigüedad (días desde la fecha de la factura), por
        proveedor. Solo facturas con pendiente > 0. El semáforo del dashboard sale de los tramos."""
        edad = hoy - FacturaProveedor.fecha

        def tramo(desde: int, hasta: int | None):
            cond = edad >= desde
            if hasta is not None:
                cond = cond & (edad <= hasta)
            return func.coalesce(
                func.sum(sa_case((cond, FacturaProveedor.pendiente), else_=0)), 0
            )

        filas = (
            await self._s.execute(
                select(
                    FacturaProveedor.proveedor,
                    func.coalesce(func.sum(FacturaProveedor.pendiente), 0).label("total"),
                    tramo(0, 30).label("d0_30"),
                    tramo(31, 60).label("d31_60"),
                    tramo(61, 90).label("d61_90"),
                    tramo(91, None).label("d90_mas"),
                    func.count().label("facturas"),
                    func.max(edad).label("mas_vieja"),
                )
                .where(FacturaProveedor.pendiente > 0)
                .group_by(FacturaProveedor.proveedor)
                .order_by(func.sum(FacturaProveedor.pendiente).desc())
            )
        ).all()
        return [
            AgingProveedorFila(
                proveedor=f.proveedor, total_pendiente=Decimal(f.total),
                d0_30=Decimal(f.d0_30), d31_60=Decimal(f.d31_60),
                d61_90=Decimal(f.d61_90), d90_mas=Decimal(f.d90_mas),
                facturas=int(f.facturas), mas_vieja_dias=int(f.mas_vieja),
            )
            for f in filas
        ]

    async def gastos_por_dia(
        self, *, inicio: datetime, fin: datetime
    ) -> list[tuple[date, Decimal]]:
        """Gastos agrupados por día Colombia del rango (para la proyección y el calendario)."""
        dia = func.date(func.timezone("America/Bogota", Gasto.creado_en)).label("dia")
        filas = (
            await self._s.execute(
                select(dia, func.coalesce(func.sum(Gasto.monto), 0).label("total"))
                .where(Gasto.creado_en >= inicio, Gasto.creado_en <= fin)
                .group_by(dia)
                .order_by(dia)
            )
        ).all()
        return [(f.dia, Decimal(f.total)) for f in filas]

    async def calendario(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> list[DiaCalendario]:
        """Agregado diario del mes para el heatmap: total vendido, # transacciones y gastos."""
        dia = func.date(func.timezone("America/Bogota", Venta.fecha)).label("dia")
        condiciones = [Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        filas_v = (
            await self._s.execute(
                select(
                    dia,
                    func.coalesce(func.sum(Venta.total), 0).label("total"),
                    func.count().label("num"),
                )
                .where(*condiciones)
                .group_by(dia)
            )
        ).all()
        gastos = dict(await self.gastos_por_dia(inicio=inicio, fin=fin))
        por_dia = {
            f.dia: DiaCalendario(
                fecha=f.dia, total=Decimal(f.total), num_ventas=int(f.num),
                gastos=gastos.get(f.dia, Decimal("0")),
            )
            for f in filas_v
        }
        # Días con gastos pero sin ventas también aparecen (el heatmap pinta el gasto).
        for d, g in gastos.items():
            if d not in por_dia:
                por_dia[d] = DiaCalendario(fecha=d, total=Decimal("0"), num_ventas=0, gastos=g)
        return sorted(por_dia.values(), key=lambda x: x.fecha)

    async def estado_pedidos_proveedor(
        self, *, hoy: date, ahora: datetime
    ) -> EstadoPedidosProveedor:
        """Pedidos en camino + demorados (pasaron su fecha estimada o el promedio del proveedor)."""
        fila = (
            await self._s.execute(
                text(
                    "WITH prom AS ("
                    "  SELECT proveedor_id, "
                    "         AVG(EXTRACT(EPOCH FROM (fecha_recepcion - fecha_pedido)) / 3600.0) AS h "
                    "  FROM pedidos_proveedor WHERE estado = 'recibido' GROUP BY proveedor_id) "
                    "SELECT COUNT(*) AS en_camino, "
                    "  COUNT(*) FILTER (WHERE "
                    "    (pp.fecha_estimada IS NOT NULL AND pp.fecha_estimada < :hoy) "
                    "    OR (prom.h IS NOT NULL AND "
                    "        EXTRACT(EPOCH FROM (CAST(:ahora AS timestamptz) - pp.fecha_pedido)) / 3600.0 > prom.h)"
                    "  ) AS demorados, "
                    "  MIN(pp.fecha_pedido) AS mas_viejo "
                    "FROM pedidos_proveedor pp "
                    "LEFT JOIN prom ON prom.proveedor_id = pp.proveedor_id "
                    "WHERE pp.estado = 'pedido'"
                ),
                {"hoy": hoy, "ahora": ahora},
            )
        ).one()
        mas_viejo_horas = (
            round((ahora - fila.mas_viejo).total_seconds() / 3600.0, 2)
            if fila.mas_viejo is not None else None
        )
        return EstadoPedidosProveedor(
            en_camino=int(fila.en_camino), demorados=int(fila.demorados),
            mas_viejo_horas=mas_viejo_horas,
        )

    async def vencimientos_cxp(self, *, hoy: date) -> VencimientosCxP:
        """CxP con `fecha_vencimiento` explícita: vencidas y por vencer en 7 días (pendiente > 0)."""
        limite = hoy + timedelta(days=7)
        fila = (
            await self._s.execute(
                select(
                    func.count().filter(FacturaProveedor.fecha_vencimiento < hoy).label("venc"),
                    func.coalesce(func.sum(FacturaProveedor.pendiente).filter(
                        FacturaProveedor.fecha_vencimiento < hoy
                    ), 0).label("monto_venc"),
                    func.count().filter(
                        FacturaProveedor.fecha_vencimiento >= hoy,
                        FacturaProveedor.fecha_vencimiento <= limite,
                    ).label("prox"),
                    func.coalesce(func.sum(FacturaProveedor.pendiente).filter(
                        FacturaProveedor.fecha_vencimiento >= hoy,
                        FacturaProveedor.fecha_vencimiento <= limite,
                    ), 0).label("monto_prox"),
                ).where(
                    FacturaProveedor.pendiente > 0,
                    FacturaProveedor.fecha_vencimiento.is_not(None),
                )
            )
        ).one()
        return VencimientosCxP(
            vencidas=int(fila.venc), monto_vencido=Decimal(fila.monto_venc),
            por_vencer_7d=int(fila.prox), monto_por_vencer=Decimal(fila.monto_prox),
        )

    async def total_fiado(self) -> Decimal:
        """Cartera de fiados viva (Σ saldo > 0): plata de la ferretería en la calle."""
        total = (
            await self._s.execute(
                text("SELECT COALESCE(SUM(saldo), 0) FROM fiados WHERE saldo > 0")
            )
        ).scalar_one()
        return Decimal(total)

    async def inventario_confiable(self) -> InventarioConfiable:
        """Avance del inventario progresivo (0052): cuántos productos activos ya se cuadraron y el
        stock bajo SOLO entre confiables (los no cuadrados en negativo no son alerta, son backlog)."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT COUNT(*) FILTER (WHERE p.activo) AS activos, "
                    "  COUNT(*) FILTER (WHERE p.activo AND i.cuadrado_at IS NOT NULL) AS cuadrados, "
                    "  COUNT(*) FILTER (WHERE p.activo AND i.cuadrado_at IS NOT NULL "
                    "                   AND i.stock_actual < i.stock_minimo) AS bajo "
                    "FROM productos p LEFT JOIN inventario i ON i.producto_id = p.id"
                )
            )
        ).one()
        return InventarioConfiable(
            productos_activos=int(fila.activos), productos_cuadrados=int(fila.cuadrados),
            stock_bajo_confiables=int(fila.bajo),
        )

    async def caja_abierta_empresa(self) -> bool:
        """¿Hay ALGUNA caja abierta en la empresa? (alerta 'abre la caja' del cockpit)."""
        return (
            await self._s.execute(
                text("SELECT 1 FROM caja WHERE estado = 'abierta' LIMIT 1")
            )
        ).scalar_one_or_none() is not None

    async def top_productos(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None, limite: int
    ) -> list[TopProductoFila]:
        """Ranking de productos por ingreso (cantidad × precio) en el rango, de ventas completadas.

        Agrupa `ventas_detalle` (join `ventas` y `productos`), excluye las varia (sin producto_id) y
        las ventas anuladas. `vendedor_id` lo acota a un vendedor; `None` = todo el negocio. Orden por
        ingreso descendente.
        """
        ingreso_expr = func.coalesce(
            func.sum(VentaDetalle.cantidad * VentaDetalle.precio_unitario), 0
        )
        condiciones = [
            Venta.estado == "completada",
            Venta.fecha >= inicio,
            Venta.fecha <= fin,
            VentaDetalle.producto_id.is_not(None),
        ]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        stmt = (
            select(
                VentaDetalle.producto_id,
                Producto.nombre,
                func.coalesce(func.sum(VentaDetalle.cantidad), 0).label("cantidad"),
                ingreso_expr.label("ingreso"),
            )
            .join(Venta, Venta.id == VentaDetalle.venta_id)
            .join(Producto, Producto.id == VentaDetalle.producto_id)
            .where(*condiciones)
            .group_by(VentaDetalle.producto_id, Producto.nombre)
            .order_by(ingreso_expr.desc())
            .limit(limite)
        )
        filas = (await self._s.execute(stmt)).all()
        return [
            TopProductoFila(
                producto_id=fila.producto_id, nombre=fila.nombre,
                cantidad=Decimal(fila.cantidad), ingreso=Decimal(fila.ingreso),
            )
            for fila in filas
        ]
