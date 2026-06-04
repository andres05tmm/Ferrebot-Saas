"""Servicio de inventario: orquesta búsqueda, cálculo de precio y ajuste de stock.

El SQL vive en SqlInventarioRepository; aquí solo va la lógica de dominio (regla de precio,
idempotencia del ajuste, validación de stock negativo). El ajuste corre en una transacción:
lock de la fila de inventario → movimiento AJUSTE → actualización de stock → evento.
"""
from dataclasses import dataclass
from decimal import Decimal

from modules.inventario.busqueda import BuscadorProductos, ResultadoBusqueda
from modules.inventario.errors import AjusteDejaStockNegativo, ProductoInexistente
from modules.inventario.models import Producto
from modules.inventario.precios import (
    EsquemaPrecio,
    FraccionPrecio,
    obtener_precio_para_cantidad,
    regla_para_cantidad,
)
from modules.inventario.repository import SqlInventarioRepository


def esquema_de(producto: Producto) -> EsquemaPrecio:
    """Construye el esquema de precio que consume el motor a partir del producto ORM."""
    return EsquemaPrecio(
        precio_venta=producto.precio_venta,
        precio_umbral=producto.precio_umbral,
        precio_bajo_umbral=producto.precio_bajo_umbral,
        precio_sobre_umbral=producto.precio_sobre_umbral,
        fracciones=tuple(
            FraccionPrecio(decimal=fr.decimal, precio_total=fr.precio_total)
            for fr in producto.fracciones
        ),
    )


@dataclass(frozen=True, slots=True)
class PrecioCalculado:
    producto_id: int
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal
    regla: str


@dataclass(frozen=True, slots=True)
class AjusteResultado:
    producto_id: int
    delta: Decimal
    stock_actual: Decimal
    replay: bool
    movimiento_id: int


class InventarioService:
    def __init__(self, repo: SqlInventarioRepository) -> None:
        self._repo = repo

    async def buscar(self, query: str, *, limite: int = 10) -> ResultadoBusqueda:
        return await BuscadorProductos(self._repo).buscar(query, limite=limite)

    async def calcular_precio(self, producto_id: int, cantidad: Decimal) -> PrecioCalculado:
        producto = await self._repo.obtener_producto(producto_id)
        if producto is None:
            raise ProductoInexistente(producto_id)
        esquema = esquema_de(producto)
        total, precio_unitario = obtener_precio_para_cantidad(esquema, cantidad)
        return PrecioCalculado(
            producto_id=producto_id,
            cantidad=cantidad,
            precio_unitario=precio_unitario,
            total=total,
            regla=regla_para_cantidad(esquema, cantidad),
        )

    async def ajustar(
        self,
        *,
        producto_id: int,
        delta: Decimal,
        motivo: str,
        usuario_id: int | None,
        idempotency_key: str | None = None,
    ) -> AjusteResultado:
        # Idempotencia estructural: si la key ya tiene movimiento, se devuelve ese (sin re-aplicar).
        if idempotency_key:
            previo = await self._repo.ajuste_por_key(idempotency_key)
            if previo is not None:
                stock = await self._repo.stock_actual(previo.producto_id)
                return AjusteResultado(
                    previo.producto_id, previo.cantidad, stock or Decimal("0"),
                    replay=True, movimiento_id=previo.id,
                )

        producto = await self._repo.obtener_producto(producto_id)
        if producto is None:
            raise ProductoInexistente(producto_id)

        # Lock de la fila de inventario: serializa el cálculo de stock entre ajustes concurrentes.
        actual = await self._repo.lock_stock(producto_id)
        base = actual if actual is not None else Decimal("0")
        nuevo = base + delta
        if nuevo < 0:
            raise AjusteDejaStockNegativo(producto_id, base, delta)

        movimiento_id = await self._repo.aplicar_ajuste(
            producto_id=producto_id, delta=delta, nuevo_stock=nuevo,
            referencia=motivo, usuario_id=usuario_id, idempotency_key=idempotency_key,
        )
        return AjusteResultado(producto_id, delta, nuevo, replay=False, movimiento_id=movimiento_id)
