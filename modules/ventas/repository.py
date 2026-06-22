"""Repositorio de ventas: único lugar con SQL (regla no negociable #2).

Inserta venta + detalle + movimientos_inventario y descuenta stock en UNA transacción
(la sesión del tenant); el consecutivo sale de la SEQUENCE; emite el evento pg_notify.
El stock se bloquea con SELECT ... FOR UPDATE en lock_inventario (evita carreras).
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, selectinload

from core.config.timezone import now_co, rango_dia_co
from core.events import publish
from modules.facturacion.models import FacturaElectronica
from modules.inventario.busqueda import BuscadorProductos
from modules.inventario.models import Inventario, MovimientoInventario, Producto
from modules.inventario.precios import FraccionPrecio
from modules.inventario.repository import SqlInventarioRepository
from modules.ventas.models import Venta, VentaDetalle
from modules.ventas.schemas import VentaConLineas, VentaDetalleLeer, VentaLeer
from modules.ventas.service import (
    EdicionVenta,
    FraccionBusqueda,
    ProductoBusqueda,
    ProductoPrecio,
    VentaHeader,
)

# Estados de factura electrónica que BLOQUEAN el borrado de la venta (factura "viva").
_ESTADOS_FACTURA_VIVA = ("pendiente", "aceptada")


class SqlVentasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._locked: dict[int, Inventario] = {}

    async def buscar_por_idempotency(self, key: str) -> VentaLeer | None:
        venta = (
            await self._s.execute(select(Venta).where(Venta.idempotency_key == key))
        ).scalar_one_or_none()
        return VentaLeer.model_validate(venta) if venta is not None else None

    async def listar(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        vendedor_id: int | None = None,
    ) -> list[VentaLeer]:
        """Ventas del rango (hora Colombia; default = hoy), fecha DESC. Incluye anuladas (el
        estado va en `VentaLeer`). `vendedor_id` acota a un vendedor; `None` = todas. No carga el
        detalle (`lazyload`): la lista no lo necesita."""
        inicio, fin = rango_dia_co(desde, hasta)
        stmt = select(Venta).where(Venta.fecha >= inicio, Venta.fecha <= fin)
        if vendedor_id is not None:
            stmt = stmt.where(Venta.vendedor_id == vendedor_id)
        stmt = stmt.order_by(Venta.fecha.desc()).options(lazyload(Venta.detalles))
        ventas = (await self._s.execute(stmt)).scalars().all()
        return [VentaLeer.model_validate(v) for v in ventas]

    async def obtener_cabecera(self, venta_id: int) -> VentaLeer | None:
        """Cabecera de una venta (sin líneas) para los guards del borrado: fecha y vendedor_id."""
        venta = (
            await self._s.execute(
                select(Venta).where(Venta.id == venta_id).options(lazyload(Venta.detalles))
            )
        ).scalar_one_or_none()
        return VentaLeer.model_validate(venta) if venta is not None else None

    async def tiene_factura_viva(self, venta_id: int) -> bool:
        """¿La venta tiene una factura electrónica VIVA (estado pendiente/aceptada)?

        Lectura cross-módulo a `facturas_electronicas` (SQL solo en el repo, regla #2): bloquea el
        borrado si hay un documento fiscal en curso o aceptado por la DIAN.
        """
        fila = (
            await self._s.execute(
                select(FacturaElectronica.id)
                .where(
                    FacturaElectronica.venta_id == venta_id,
                    FacturaElectronica.estado.in_(_ESTADOS_FACTURA_VIVA),
                )
                .limit(1)
            )
        ).first()
        return fila is not None

    async def borrar_venta(self, venta_id: int) -> None:
        """Borra una venta de forma TOTAL (física) restaurando stock, en una transacción.

        Por cada línea de catálogo restaura el stock (`stock_actual += cantidad`, fila bloqueada) y
        borra el movimiento SALIDA de la venta: el stock vuelve a su valor previo y su movimiento
        desaparece — neto cero, como si la venta no hubiera ocurrido (respeta la regla #7). Luego
        borra la venta (cascade a `ventas_detalle`) y emite `venta_anulada` + `inventario_actualizado`.
        """
        venta = (
            await self._s.execute(
                select(Venta).where(Venta.id == venta_id).options(selectinload(Venta.detalles))
            )
        ).scalar_one_or_none()
        if venta is None:
            return
        consecutivo = venta.consecutivo  # capturar antes de borrar (el objeto queda expirado tras delete)

        await self._revertir_stock_y_salidas(venta)
        await self._s.delete(venta)
        await self._s.flush()

        await publish(self._s, "venta_anulada", {"venta_id": venta_id, "consecutivo": consecutivo})
        await publish(self._s, "inventario_actualizado", {
            "venta_id": venta_id, "accion": "venta_anulada",
        })

    async def _revertir_stock_y_salidas(self, venta: Venta) -> None:
        """Reversión de las líneas de una venta: devuelve su stock y borra sus movimientos SALIDA.

        Por cada línea de catálogo restaura el stock (`stock_actual += cantidad`, fila bloqueada con
        FOR UPDATE) y borra los SALIDA de la venta (`referencia = venta:{id}`). NO toca la venta ni su
        detalle: lo reusan tanto el borrado (que luego elimina la venta) como la edición (que reemplaza
        las líneas). Neto cero respecto al stock previo a la venta (regla #7).
        """
        for det in venta.detalles:
            if det.producto_id is None:
                continue  # línea varia: no movió inventario
            inv = (
                await self._s.execute(
                    select(Inventario)
                    .where(Inventario.producto_id == det.producto_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if inv is not None:
                inv.stock_actual = inv.stock_actual + det.cantidad

        await self._s.execute(
            delete(MovimientoInventario).where(
                MovimientoInventario.referencia == f"venta:{venta.id}",
                MovimientoInventario.tipo == "SALIDA",
            )
        )

    async def revertir_lineas(self, venta_id: int) -> None:
        """Revierte las líneas de una venta SIN borrarla (para la edición en el lugar).

        Restaura el stock y borra los SALIDA de las líneas viejas (reusa `_revertir_stock_y_salidas`)
        y vacía el detalle viejo (`detalles = []` → delete-orphan en el flush). La venta queda lista
        para recibir el detalle nuevo en `aplicar_edicion`.
        """
        venta = (
            await self._s.execute(
                select(Venta).where(Venta.id == venta_id).options(selectinload(Venta.detalles))
            )
        ).scalar_one_or_none()
        if venta is None:
            return
        await self._revertir_stock_y_salidas(venta)
        venta.detalles = []
        await self._s.flush()

    async def aplicar_edicion(self, venta_id: int, edicion: "EdicionVenta") -> VentaConLineas | None:
        """Aplica las líneas nuevas a una venta YA revertida, EN EL LUGAR (mismo id/consecutivo/fecha).

        Actualiza cabecera (cliente_id/metodo_pago + totales recalculados), inserta el detalle nuevo y
        sus movimientos SALIDA (descuenta stock de las filas ya bloqueadas por `lock_inventario`; permite
        negativo en modo permisivo). Emite `venta_editada` + `inventario_actualizado` y devuelve la venta
        con sus líneas. Debe llamarse tras `revertir_lineas` (y tras resolver las líneas en el servicio).
        """
        venta = (
            await self._s.execute(
                select(Venta).where(Venta.id == venta_id).options(selectinload(Venta.detalles))
            )
        ).scalar_one_or_none()
        if venta is None:
            return None

        venta.cliente_id = edicion.cliente_id
        venta.metodo_pago = edicion.metodo_pago
        venta.subtotal = edicion.subtotal
        venta.impuestos = edicion.impuestos
        venta.total = edicion.total
        for ln in edicion.lineas:
            venta.detalles.append(VentaDetalle(
                producto_id=ln.producto_id, descripcion=ln.descripcion, cantidad=ln.cantidad,
                precio_unitario=ln.precio_unitario, iva=ln.iva,
            ))
        await self._s.flush()

        for ln in edicion.lineas:
            if not ln.descontar_stock or ln.producto_id is None:
                continue
            inv = self._locked[ln.producto_id]
            inv.stock_actual = inv.stock_actual - ln.cantidad
            self._s.add(MovimientoInventario(
                producto_id=ln.producto_id, tipo="SALIDA", cantidad=ln.cantidad,
                costo_unitario=ln.costo_unitario, referencia=f"venta:{venta.id}",
                usuario_id=venta.vendedor_id,
            ))
        await self._s.flush()

        await publish(self._s, "venta_editada", {
            "venta_id": venta.id, "consecutivo": venta.consecutivo, "total": str(venta.total),
        })
        await publish(self._s, "inventario_actualizado", {
            "venta_id": venta.id, "accion": "venta_editada",
        })
        cabecera = VentaLeer.model_validate(venta)
        lineas = [VentaDetalleLeer.model_validate(d) for d in venta.detalles]
        return VentaConLineas(**cabecera.model_dump(), lineas=lineas)

    async def obtener(self, venta_id: int) -> VentaConLineas | None:
        """Detalle de una venta con sus líneas (carga `detalles` con selectin, no lazy)."""
        venta = (
            await self._s.execute(
                select(Venta).where(Venta.id == venta_id).options(selectinload(Venta.detalles))
            )
        ).scalar_one_or_none()
        if venta is None:
            return None
        cabecera = VentaLeer.model_validate(venta)
        lineas = [VentaDetalleLeer.model_validate(d) for d in venta.detalles]
        return VentaConLineas(**cabecera.model_dump(), lineas=lineas)

    async def obtener_producto(self, producto_id: int) -> ProductoPrecio | None:
        prod = (
            await self._s.execute(select(Producto).where(Producto.id == producto_id))
        ).scalar_one_or_none()
        if prod is None:
            return None
        fracciones = tuple(
            FraccionPrecio(decimal=fr.decimal, precio_total=fr.precio_total)
            for fr in prod.fracciones
        )
        return ProductoPrecio(
            id=prod.id, nombre=prod.nombre, precio_venta=prod.precio_venta,
            iva=prod.iva, activo=prod.activo, precio_compra=prod.precio_compra,
            precio_umbral=prod.precio_umbral,
            precio_bajo_umbral=prod.precio_bajo_umbral,
            precio_sobre_umbral=prod.precio_sobre_umbral,
            fracciones=fracciones,
            unidad_medida=prod.unidad_medida,
        )

    async def lock_inventario(self, producto_id: int) -> Decimal | None:
        inv = (
            await self._s.execute(
                select(Inventario).where(Inventario.producto_id == producto_id).with_for_update()
            )
        ).scalar_one_or_none()
        if inv is None:
            return None
        self._locked[producto_id] = inv
        return inv.stock_actual

    async def stock_sin_lock(self, producto_id: int) -> Decimal | None:
        """Stock actual SIN bloquear la fila: lectura para CONSULTA (nunca para vender).

        A diferencia de `lock_inventario` (FOR UPDATE, camino de escritura), no toma lock: la consulta
        del bot solo lee. None si el producto no tiene fila de inventario.
        """
        return (
            await self._s.execute(
                select(Inventario.stock_actual).where(Inventario.producto_id == producto_id)
            )
        ).scalar_one_or_none()

    async def obtener_producto_busqueda(self, producto_id: int) -> ProductoBusqueda | None:
        """Datos de un producto para la consulta del bot: nombre, precio base, unidad, stock y sus
        fracciones con la etiqueta de texto (`productos_fracciones.fraccion`) y su precio total.

        Solo lectura. Las fracciones (relación `selectin`, sin N+1) van de mayor a menor para que la
        más grande aparezca primero. None si el producto no existe.
        """
        prod = (
            await self._s.execute(select(Producto).where(Producto.id == producto_id))
        ).scalar_one_or_none()
        if prod is None:
            return None
        stock = await self.stock_sin_lock(producto_id)
        fracciones = tuple(
            FraccionBusqueda(etiqueta=fr.fraccion, precio_total=fr.precio_total)
            for fr in sorted(prod.fracciones, key=lambda f: f.decimal or Decimal("0"), reverse=True)
        )
        return ProductoBusqueda(
            id=prod.id, nombre=prod.nombre, precio=prod.precio_venta,
            stock=stock if stock is not None else Decimal("0"),
            unidad_medida=prod.unidad_medida, fracciones=fracciones,
        )

    async def buscar_productos_por_nombre(
        self, texto: str, *, limite: int = 10
    ) -> list[tuple[int, str]]:
        """Candidatos (id, nombre) por nombre reusando el buscador de inventario (misma resolución
        de 4 capas que el resto del sistema: exacta → alias → trigram → fuzzy), sobre la sesión del
        tenant. Acotado por `limite`. Quien arma precio/stock por candidato es el servicio.
        """
        buscador = BuscadorProductos(SqlInventarioRepository(self._s))
        resultado = await buscador.buscar(texto, limite=limite)
        return [(c.producto_id, c.nombre) for c in resultado.coincidencias]

    async def siguiente_consecutivo(self) -> int:
        return (await self._s.execute(text("SELECT nextval('ventas_consecutivo_seq')"))).scalar_one()

    async def crear_venta(self, header: VentaHeader) -> VentaLeer:
        venta = Venta(
            consecutivo=header.consecutivo,
            cliente_id=header.cliente_id,
            vendedor_id=header.vendedor_id,
            fecha=now_co(),
            subtotal=header.subtotal,
            impuestos=header.impuestos,
            total=header.total,
            metodo_pago=header.metodo_pago,
            origen=header.origen,
            idempotency_key=header.idempotency_key,
        )
        for ln in header.lineas:
            venta.detalles.append(VentaDetalle(
                producto_id=ln.producto_id, descripcion=ln.descripcion, cantidad=ln.cantidad,
                precio_unitario=ln.precio_unitario, iva=ln.iva,
            ))
        self._s.add(venta)
        await self._s.flush()  # asigna venta.id

        for ln in header.lineas:
            if not ln.descontar_stock or ln.producto_id is None:
                continue
            inv = self._locked[ln.producto_id]
            inv.stock_actual = inv.stock_actual - ln.cantidad
            self._s.add(MovimientoInventario(
                producto_id=ln.producto_id, tipo="SALIDA", cantidad=ln.cantidad,
                costo_unitario=ln.costo_unitario, referencia=f"venta:{venta.id}",
                usuario_id=header.vendedor_id,
            ))
        await self._s.flush()

        await publish(self._s, "venta_registrada", {
            "venta_id": venta.id,
            "consecutivo": venta.consecutivo,
            "total": str(venta.total),
            "metodo_pago": venta.metodo_pago,
            "origen": venta.origen,
        })
        return VentaLeer.model_validate(venta)
