"""Repositorio de devoluciones: ÚNICO lugar con SQL (regla no negociable #2).

Sesión del tenant (la base es la frontera). Re-ingresa stock con un movimiento `DEVOLUCION` cuyo
`costo_unitario` es el SNAPSHOT de la SALIDA original (COGS exacto, no el promedio del día), en la
misma transacción que la cabecera y su contrapartida de dinero. El stock se bloquea con FOR UPDATE.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events import publish
from modules.devoluciones.models import Devolucion, DevolucionDetalle
from modules.devoluciones.schemas import DevolucionLeer
from modules.fiados.models import Fiado
from modules.inventario.models import Inventario, MovimientoInventario


@dataclass(frozen=True, slots=True)
class CabeceraVenta:
    id: int
    vendedor_id: int
    metodo_pago: str
    estado: str


@dataclass(frozen=True, slots=True)
class LineaVendida:
    """Una línea de la venta con su costo snapshot (de la SALIDA original) para resolver la devolución."""

    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    costo_unitario: Decimal | None


@dataclass(frozen=True, slots=True)
class LineaResueltaDev:
    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    costo_unitario: Decimal | None
    total_linea: Decimal


@dataclass(frozen=True, slots=True)
class DatosVenta:
    cabecera: CabeceraVenta
    lineas: list[LineaVendida] = field(default_factory=list)


class SqlDevolucionesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def buscar_por_idempotency(self, key: str) -> Devolucion | None:
        """Devolución ya registrada con esa key (con sus detalles cargados por selectin), o None."""
        return (
            await self._s.execute(select(Devolucion).where(Devolucion.idempotency_key == key))
        ).scalar_one_or_none()

    async def cabecera_venta(self, venta_id: int) -> CabeceraVenta | None:
        row = (
            await self._s.execute(
                text("SELECT id, vendedor_id, metodo_pago, estado FROM ventas WHERE id=:v"),
                {"v": venta_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return CabeceraVenta(id=row.id, vendedor_id=row.vendedor_id, metodo_pago=row.metodo_pago, estado=row.estado)

    async def lineas_vendidas(self, venta_id: int) -> list[LineaVendida]:
        """Líneas de la venta con el costo snapshot hilado desde la SALIDA original (por producto).

        El costo lo aporta `movimientos_inventario.costo_unitario` de la SALIDA (`referencia=venta:{id}`),
        no `productos.costo_promedio` actual: así la devolución re-ingresa al costo con que salió."""
        costos = {
            r.producto_id: r.costo_unitario
            for r in (
                await self._s.execute(
                    text(
                        "SELECT producto_id, costo_unitario FROM movimientos_inventario "
                        "WHERE referencia=:r AND tipo='SALIDA'"
                    ),
                    {"r": f"venta:{venta_id}"},
                )
            ).all()
        }
        filas = (
            await self._s.execute(
                text(
                    "SELECT producto_id, descripcion, cantidad, precio_unitario "
                    "FROM ventas_detalle WHERE venta_id=:v ORDER BY id"
                ),
                {"v": venta_id},
            )
        ).all()
        return [
            LineaVendida(
                producto_id=f.producto_id, descripcion=f.descripcion, cantidad=f.cantidad,
                precio_unitario=f.precio_unitario,
                costo_unitario=costos.get(f.producto_id) if f.producto_id is not None else None,
            )
            for f in filas
        ]

    async def devuelto_por_venta(self, venta_id: int) -> dict[int | None, Decimal]:
        """Cantidad YA devuelta por producto en devoluciones previas de la venta (guard anti sobre-devolución).

        Clave None agrupa las líneas varias (sin producto_id). Vacío si la venta no tiene devoluciones."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT dd.producto_id, COALESCE(SUM(dd.cantidad), 0) AS cantidad "
                    "FROM devoluciones_detalle dd JOIN devoluciones d ON d.id = dd.devolucion_id "
                    "WHERE d.venta_id = :v GROUP BY dd.producto_id"
                ),
                {"v": venta_id},
            )
        ).all()
        return {f.producto_id: Decimal(f.cantidad) for f in filas}

    async def factura_aceptada_de_venta(self, venta_id: int) -> int | None:
        """Id del documento fiscal ACEPTADO por DIAN de la venta (para ligar la nota crédito), o None.

        `aceptada` = transmitida a DIAN: cuando existe, la corrección va por nota crédito (no borrado)."""
        return (
            await self._s.execute(
                text(
                    "SELECT id FROM facturas_electronicas WHERE venta_id=:v AND estado='aceptada' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"v": venta_id},
            )
        ).scalar_one_or_none()

    async def fiado_de_venta(self, venta_id: int) -> Fiado | None:
        """Fiado ligado a la venta (para reintegrar por abono), bloqueado con FOR UPDATE, o None."""
        return (
            await self._s.execute(
                select(Fiado).where(Fiado.venta_id == venta_id).order_by(Fiado.id).limit(1).with_for_update()
            )
        ).scalar_one_or_none()

    async def crear_devolucion(
        self, *, venta_id: int, total: Decimal, metodo_reintegro: str, motivo: str | None,
        usuario_id: int | None, idempotency_key: str | None, lineas: list[LineaResueltaDev],
    ) -> Devolucion:
        """Inserta la cabecera + su detalle (dispara la UNIQUE de idempotency_key). Devuelve el ORM."""
        dev = Devolucion(
            venta_id=venta_id, total=total, metodo_reintegro=metodo_reintegro, motivo=motivo,
            usuario_id=usuario_id, idempotency_key=idempotency_key, estado="registrada",
        )
        for ln in lineas:
            dev.detalles.append(DevolucionDetalle(
                producto_id=ln.producto_id, descripcion=ln.descripcion, cantidad=ln.cantidad,
                precio_unitario=ln.precio_unitario, costo_unitario=ln.costo_unitario,
                total_linea=ln.total_linea,
            ))
        self._s.add(dev)
        await self._s.flush()  # asigna dev.id
        return dev

    async def reingresar_stock(
        self, devolucion_id: int, lineas: list[LineaResueltaDev], usuario_id: int | None
    ) -> None:
        """Por cada línea de catálogo: restaura stock (FOR UPDATE) y crea el movimiento DEVOLUCION.

        `costo_unitario` = snapshot de la SALIDA original; `fecha_operacion` = fecha del reintegro (hoy).
        Regla #7: la mercancía vuelve al inventario CON su movimiento, nunca stock sin movimiento."""
        fecha = now_co()
        for ln in lineas:
            if ln.producto_id is None:
                continue  # línea varia: no movió inventario al vender → tampoco al devolver
            inv = (
                await self._s.execute(
                    select(Inventario).where(Inventario.producto_id == ln.producto_id).with_for_update()
                )
            ).scalar_one_or_none()
            if inv is None:
                inv = Inventario(producto_id=ln.producto_id, stock_actual=Decimal("0"), stock_minimo=Decimal("0"))
                self._s.add(inv)
            inv.stock_actual = inv.stock_actual + ln.cantidad
            self._s.add(MovimientoInventario(
                producto_id=ln.producto_id, tipo="DEVOLUCION", cantidad=ln.cantidad,
                costo_unitario=ln.costo_unitario, referencia=f"devolucion:{devolucion_id}",
                usuario_id=usuario_id, fecha_operacion=fecha,
            ))
        await self._s.flush()

    async def vincular_nota(self, devolucion_id: int, nota_id: int) -> None:
        dev = (
            await self._s.execute(select(Devolucion).where(Devolucion.id == devolucion_id))
        ).scalar_one()
        dev.nota_id = nota_id
        await self._s.flush()

    async def emitir_evento(self, devolucion: Devolucion) -> None:
        """Publica `devolucion_registrada` + `inventario_actualizado` (SSE de la empresa)."""
        await publish(self._s, "devolucion_registrada", {
            "devolucion_id": devolucion.id, "venta_id": devolucion.venta_id,
            "total": str(devolucion.total), "metodo_reintegro": devolucion.metodo_reintegro,
        })
        await publish(self._s, "inventario_actualizado", {
            "devolucion_id": devolucion.id, "accion": "devolucion",
        })
