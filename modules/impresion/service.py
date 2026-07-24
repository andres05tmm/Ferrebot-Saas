"""Servicio de la cola de impresión (ADR 0033): cola del agente, ack, reimpresión y bajo demanda."""
from decimal import Decimal

from sqlalchemy import text

from modules.impresion.generacion import _num
from modules.impresion.models import TrabajoImpresion
from modules.impresion.repository import SqlImpresionRepository


class TrabajoInexistente(Exception):
    pass


class OrigenInvalido(Exception):
    """El pedido/venta del trabajo bajo demanda no existe."""


class ImpresionService:
    def __init__(self, repo: SqlImpresionRepository) -> None:
        self._repo = repo

    async def cola(self, *, limite: int = 50) -> list[TrabajoImpresion]:
        return await self._repo.reclamar_cola(limite=limite)

    async def ack(self, trabajo_id: int, *, ok: bool, detalle: str | None = None) -> TrabajoImpresion:
        trabajo = await self._repo.por_id(trabajo_id)
        if trabajo is None:
            raise TrabajoInexistente(str(trabajo_id))
        return await self._repo.ack(trabajo, ok=ok, detalle=detalle)

    async def reimprimir(self, trabajo_id: int) -> TrabajoImpresion:
        """Trabajo NUEVO ligado al original. Clave por # de reimpresión CERRADA: el doble clic
        colisiona (devuelve la reimpresión viva); un reintento tras imprimir crea la siguiente."""
        original = await self._repo.por_id(trabajo_id)
        if original is None:
            raise TrabajoInexistente(str(trabajo_id))
        n = await self._repo.contar_reimpresiones_cerradas(original.id) + 1
        return await self._repo.crear(
            tipo=original.tipo, payload=original.payload,
            idempotency_key=f"reimpresion:{original.id}:{n}",
            zona_id=original.zona_id, pedido_id=original.pedido_id,
            comanda_id=original.comanda_id, venta_id=original.venta_id,
            reimpresion_de=original.id,
        )

    async def crear_precuenta(self, pedido_id: int) -> TrabajoImpresion:
        """Precuenta bajo demanda: snapshot del pedido (no muta nada — ADR 0032 D4)."""
        s = self._repo.sesion
        pedido = (
            await s.execute(
                text(
                    "SELECT id, cliente_nombre, origen, subtotal, total, costo_domicilio, notas "
                    "FROM pedidos WHERE id = :p"
                ),
                {"p": pedido_id},
            )
        ).first()
        if pedido is None:
            raise OrigenInvalido(f"pedido {pedido_id}")
        items = (
            await s.execute(
                text(
                    "SELECT nombre, cantidad, precio_unitario, subtotal, modificadores "
                    "FROM pedido_items WHERE pedido_id = :p ORDER BY id"
                ),
                {"p": pedido_id},
            )
        ).all()
        payload = {
            "tipo": "precuenta", "pedido_id": pedido.id, "origen": pedido.origen,
            "cliente": pedido.cliente_nombre, "notas": pedido.notas,
            "subtotal": _num(pedido.subtotal), "total": _num(pedido.total),
            "costo_domicilio": _num(pedido.costo_domicilio),
            "items": [
                {
                    "nombre": i.nombre, "cantidad": _num(i.cantidad),
                    "precio_unitario": _num(i.precio_unitario), "subtotal": _num(i.subtotal),
                    "modificadores": i.modificadores or [],
                }
                for i in items
            ],
        }
        # La clave lleva la HUELLA del contenido (total + # ítems): el doble clic replica el mismo
        # trabajo, pero una ronda nueva cambia la huella y produce una precuenta FRESCA (una orden
        # abierta evoluciona; `pedido:v1` congelaría el snapshot viejo).
        huella = f"{_num(pedido.total)}:{len(items)}"
        return await self._repo.crear(
            tipo="precuenta", payload=payload, pedido_id=pedido_id,
            idempotency_key=f"precuenta:{pedido_id}:{huella}",
        )

    async def crear_comprobante(self, venta_id: int) -> TrabajoImpresion:
        """Comprobante de venta (no fiscal) bajo demanda, con propina discriminada por descripción."""
        s = self._repo.sesion
        venta = (
            await s.execute(
                text(
                    "SELECT id, consecutivo, fecha, subtotal, impuestos, total, metodo_pago "
                    "FROM ventas WHERE id = :v"
                ),
                {"v": venta_id},
            )
        ).first()
        if venta is None:
            raise OrigenInvalido(f"venta {venta_id}")
        detalles = (
            await s.execute(
                text(
                    "SELECT descripcion, cantidad, precio_unitario "
                    "FROM ventas_detalle WHERE venta_id = :v ORDER BY id"
                ),
                {"v": venta_id},
            )
        ).all()
        payload = {
            "tipo": "comprobante", "venta_id": venta.id, "consecutivo": venta.consecutivo,
            "fecha": str(venta.fecha), "metodo_pago": venta.metodo_pago,
            "subtotal": _num(venta.subtotal), "impuestos": _num(venta.impuestos),
            "total": _num(venta.total),
            "items": [
                {
                    "nombre": d.descripcion, "cantidad": _num(d.cantidad),
                    "precio_unitario": _num(d.precio_unitario),
                    "subtotal": _num(Decimal(d.cantidad) * Decimal(d.precio_unitario)),
                }
                for d in detalles
            ],
        }
        return await self._repo.crear(
            tipo="comprobante", payload=payload, venta_id=venta_id,
            idempotency_key=f"comprobante:{venta_id}:v1",
        )
