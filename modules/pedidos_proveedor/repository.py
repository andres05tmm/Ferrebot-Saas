"""Repositorio de pedidos a proveedor: único lugar con SQL del módulo (regla no negociable #2).

El cronómetro NO se persiste: `fecha_recepcion − fecha_pedido` se deriva en lectura (service).
Eventos por `publish()` en la misma transacción de la sesión del tenant.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.compras.models import Proveedor
from modules.pedidos_proveedor.models import PedidoProveedor, PedidoProveedorDetalle


@dataclass(frozen=True, slots=True)
class MetricasProveedorRow:
    proveedor_id: int
    proveedor_nombre: str
    pedidos_recibidos: int
    lead_time_promedio_horas: float | None
    ultima_entrega: datetime | None
    pedidos_en_camino: int
    mas_viejo_en_camino: datetime | None


class SqlPedidosProveedorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def crear(
        self,
        *,
        proveedor_id: int,
        fecha_pedido: datetime,
        fecha_estimada,
        descripcion: str | None,
        monto_estimado: Decimal | None,
        anticipo: Decimal | None,
        condicion_pago: str | None,
        usuario_id: int | None,
        notas: str | None,
        idempotency_key: str | None,
        lineas: list[tuple[int | None, str | None, Decimal, Decimal | None]],
    ) -> PedidoProveedor:
        pedido = PedidoProveedor(
            proveedor_id=proveedor_id, fecha_pedido=fecha_pedido, fecha_estimada=fecha_estimada,
            estado="pedido", descripcion=descripcion, monto_estimado=monto_estimado,
            anticipo=anticipo, condicion_pago=condicion_pago, usuario_id=usuario_id,
            notas=notas, idempotency_key=idempotency_key,
        )
        self._s.add(pedido)
        await self._s.flush()
        for producto_id, desc, cantidad, costo_estimado in lineas:
            self._s.add(PedidoProveedorDetalle(
                pedido_id=pedido.id, producto_id=producto_id, descripcion=desc,
                cantidad=cantidad, costo_estimado=costo_estimado,
            ))
        await self._s.flush()
        # Carga explícita de la relación (async no admite lazy-load al serializar el recién creado).
        await self._s.refresh(pedido, attribute_names=["detalles"])
        await publish(self._s, "pedido_proveedor_creado", {
            "pedido_id": pedido.id, "proveedor_id": proveedor_id,
            "monto_estimado": str(monto_estimado) if monto_estimado is not None else None,
        })
        return pedido

    async def por_key(self, idempotency_key: str) -> PedidoProveedor | None:
        return (
            await self._s.execute(
                select(PedidoProveedor).where(PedidoProveedor.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def obtener(self, pedido_id: int, *, lock: bool = False) -> PedidoProveedor | None:
        stmt = select(PedidoProveedor).where(PedidoProveedor.id == pedido_id)
        if lock:
            stmt = stmt.with_for_update()
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def listar(self, *, estado: str | None = None) -> list[PedidoProveedor]:
        stmt = select(PedidoProveedor)
        if estado is not None:
            stmt = stmt.where(PedidoProveedor.estado == estado)
        stmt = stmt.order_by(PedidoProveedor.fecha_pedido.desc(), PedidoProveedor.id.desc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def nombre_proveedor(self, proveedor_id: int) -> str | None:
        return (
            await self._s.execute(select(Proveedor.nombre).where(Proveedor.id == proveedor_id))
        ).scalar_one_or_none()

    async def nombres_proveedores(self, ids: list[int]) -> dict[int, str]:
        """Nombres por id en UNA consulta (evita N+1 al componer las listas)."""
        if not ids:
            return {}
        filas = (
            await self._s.execute(
                select(Proveedor.id, Proveedor.nombre).where(Proveedor.id.in_(ids))
            )
        ).all()
        return {f.id: f.nombre for f in filas}

    async def set_anticipo_movimiento(self, pedido: PedidoProveedor, movimiento_id: int) -> None:
        """Ancla el egreso de caja que pagó el anticipo (candado anti-doble-egreso)."""
        pedido.anticipo_movimiento_id = movimiento_id
        await self._s.flush()

    async def reemplazar_lineas(
        self,
        pedido: PedidoProveedor,
        lineas: list[tuple[int | None, str | None, Decimal, Decimal | None]],
    ) -> None:
        pedido.detalles.clear()
        await self._s.flush()
        for producto_id, desc, cantidad, costo_estimado in lineas:
            self._s.add(PedidoProveedorDetalle(
                pedido_id=pedido.id, producto_id=producto_id, descripcion=desc,
                cantidad=cantidad, costo_estimado=costo_estimado,
            ))
        await self._s.flush()
        await self._s.refresh(pedido, attribute_names=["detalles"])

    async def marcar_recibido(
        self,
        pedido: PedidoProveedor,
        *,
        fecha_recepcion: datetime,
        compra_id: int,
        factura_proveedor_id: str | None,
        condicion_pago: str,
        notas: str | None,
    ) -> PedidoProveedor:
        pedido.estado = "recibido"
        pedido.fecha_recepcion = fecha_recepcion
        pedido.compra_id = compra_id
        pedido.factura_proveedor_id = factura_proveedor_id
        pedido.condicion_pago = condicion_pago
        if notas:
            pedido.notas = f"{pedido.notas}\n{notas}" if pedido.notas else notas
        await self._s.flush()
        await publish(self._s, "pedido_proveedor_recibido", {
            "pedido_id": pedido.id, "proveedor_id": pedido.proveedor_id,
            "compra_id": compra_id, "factura_proveedor_id": factura_proveedor_id,
        })
        return pedido

    async def marcar_cancelado(self, pedido: PedidoProveedor, *, nota: str | None) -> PedidoProveedor:
        pedido.estado = "cancelado"
        if nota:
            pedido.notas = f"{pedido.notas}\n{nota}" if pedido.notas else nota
        await self._s.flush()
        await publish(self._s, "pedido_proveedor_cancelado", {
            "pedido_id": pedido.id, "proveedor_id": pedido.proveedor_id,
        })
        return pedido

    async def promedio_lead_time_horas(self, proveedor_id: int) -> float | None:
        """Promedio histórico (horas) entre pedido y recepción del proveedor, o None sin historial."""
        valor = (
            await self._s.execute(
                text(
                    "SELECT AVG(EXTRACT(EPOCH FROM (fecha_recepcion - fecha_pedido)) / 3600.0) "
                    "FROM pedidos_proveedor WHERE proveedor_id = :p AND estado = 'recibido'"
                ),
                {"p": proveedor_id},
            )
        ).scalar_one_or_none()
        return float(valor) if valor is not None else None

    async def metricas_por_proveedor(self) -> list[MetricasProveedorRow]:
        """Lead time promedio, última entrega y pedidos en camino por proveedor (una consulta)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT pr.id, pr.nombre, "
                    "COUNT(*) FILTER (WHERE pp.estado = 'recibido') AS recibidos, "
                    "AVG(EXTRACT(EPOCH FROM (pp.fecha_recepcion - pp.fecha_pedido)) / 3600.0) "
                    "  FILTER (WHERE pp.estado = 'recibido') AS lead_prom, "
                    "MAX(pp.fecha_recepcion) FILTER (WHERE pp.estado = 'recibido') AS ultima, "
                    "COUNT(*) FILTER (WHERE pp.estado = 'pedido') AS en_camino, "
                    "MIN(pp.fecha_pedido) FILTER (WHERE pp.estado = 'pedido') AS mas_viejo "
                    "FROM pedidos_proveedor pp JOIN proveedores pr ON pr.id = pp.proveedor_id "
                    "GROUP BY pr.id, pr.nombre ORDER BY en_camino DESC, pr.nombre"
                )
            )
        ).all()
        return [
            MetricasProveedorRow(
                proveedor_id=f.id, proveedor_nombre=f.nombre,
                pedidos_recibidos=int(f.recibidos),
                lead_time_promedio_horas=float(f.lead_prom) if f.lead_prom is not None else None,
                ultima_entrega=f.ultima, pedidos_en_camino=int(f.en_camino),
                mas_viejo_en_camino=f.mas_viejo,
            )
            for f in filas
        ]

    async def stock_actual(self, producto_id: int) -> Decimal:
        """Stock actual del producto (0 si no tiene fila) — para el reporte de cuadre de la recepción."""
        valor = (
            await self._s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": producto_id}
            )
        ).scalar_one_or_none()
        return Decimal(valor) if valor is not None else Decimal("0")
