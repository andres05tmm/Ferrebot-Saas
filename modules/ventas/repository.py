"""Repositorio de ventas: único lugar con SQL (regla no negociable #2).

Inserta venta + detalle + movimientos_inventario y descuenta stock en UNA transacción
(la sesión del tenant); el consecutivo sale de la SEQUENCE; emite el evento pg_notify.
El stock se bloquea con SELECT ... FOR UPDATE en lock_inventario (evita carreras).
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, selectinload

from core.config.timezone import now_co, rango_dia_co
from core.events import publish
from modules.inventario.models import Inventario, MovimientoInventario, Producto
from modules.inventario.precios import FraccionPrecio
from modules.ventas.models import Venta, VentaDetalle
from modules.ventas.schemas import VentaConLineas, VentaDetalleLeer, VentaLeer
from modules.ventas.service import ProductoPrecio, VentaHeader


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
            iva=prod.iva, activo=prod.activo,
            precio_umbral=prod.precio_umbral,
            precio_bajo_umbral=prod.precio_bajo_umbral,
            precio_sobre_umbral=prod.precio_sobre_umbral,
            fracciones=fracciones,
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
                referencia=f"venta:{venta.id}", usuario_id=header.vendedor_id,
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
