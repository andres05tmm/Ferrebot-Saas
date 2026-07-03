"""Repositorio de compras: único lugar con SQL del módulo (regla no negociable #2).

Registrar una compra suma stock SIEMPRE por un movimiento ENTRADA (regla #7) y fija el costo de
compra del producto al costo de esa compra; el costo ya grabado en ventas pasadas NO se toca. Todo
corre en la transacción de la sesión del tenant; emite eventos por `publish()`.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from core.money import cuantizar
from modules.compras.models import Compra, CompraDetalle, Proveedor
from modules.compras.schemas import CompraLeer
from modules.inventario.models import Inventario, MovimientoInventario


@dataclass(frozen=True, slots=True)
class ItemCompra:
    """Una línea ya validada (producto, cantidad, costo) lista para persistir."""

    producto_id: int
    cantidad: Decimal
    costo: Decimal


def _promedio_ponderado(
    stock_prev: Decimal, promedio_actual: Decimal | None, cantidad: Decimal, costo: Decimal
) -> Decimal:
    """Promedio ponderado móvil (ADR 0025): (stock·promedio + cantidad·costo) / (stock + cantidad).

    Función pura. `promedio_actual` NULL (producto sin costo previo) → el promedio arranca en el costo
    de esta compra. El stock previo negativo (modo permisivo) se trata como 0: un inventario en rojo no
    aporta valor promediable. Si el denominador no es positivo (p. ej. cantidad 0) se cae al costo de la
    compra para evitar división por cero. El resultado se cuantiza a centavos (core.money).
    """
    base = promedio_actual if promedio_actual is not None else costo
    stock_eff = stock_prev if stock_prev > 0 else Decimal("0")
    denom = stock_eff + cantidad
    if denom <= 0:
        return cuantizar(costo)
    return cuantizar((stock_eff * base + cantidad * costo) / denom)


@dataclass(frozen=True, slots=True)
class CompraIdempotente:
    """Foto de una compra ya registrada bajo una `idempotency_key`, para comparar el payload (§4).

    Lleva lo necesario para decidir replay vs conflicto sin re-resolver el proveedor: la cabecera, el
    total y las líneas (producto_id, cantidad, costo).
    """

    compra: CompraLeer
    total: Decimal
    items: tuple[tuple[int, Decimal, Decimal], ...]


class SqlComprasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create_proveedor(
        self, *, proveedor_id: int | None = None, nombre: str | None = None, nit: str | None = None
    ) -> int:
        """Devuelve el id del proveedor: el dado, o uno existente (por nit/nombre), o uno nuevo."""
        if proveedor_id is not None:
            return proveedor_id
        if nit:
            existente = (
                await self._s.execute(select(Proveedor.id).where(Proveedor.nit == nit).limit(1))
            ).scalar_one_or_none()
            if existente is not None:
                return existente
        if nombre:
            existente = (
                await self._s.execute(
                    select(Proveedor.id)
                    .where(func.lower(func.btrim(Proveedor.nombre)) == nombre.strip().lower())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existente is not None:
                return existente
        prov = Proveedor(nombre=(nombre or "Proveedor").strip(), nit=nit)
        self._s.add(prov)
        await self._s.flush()
        return prov.id

    async def buscar_por_idempotency(self, key: str) -> CompraIdempotente | None:
        """Compra ya registrada bajo `key` (con su total y líneas), o None. Para el guard de §4."""
        compra_id = (
            await self._s.execute(select(Compra.id).where(Compra.idempotency_key == key))
        ).scalar_one_or_none()
        if compra_id is None:
            return None
        compra = await self._leer(compra_id)
        filas = (
            await self._s.execute(
                select(CompraDetalle.producto_id, CompraDetalle.cantidad, CompraDetalle.costo)
                .where(CompraDetalle.compra_id == compra_id)
            )
        ).all()
        items = tuple((f.producto_id, f.cantidad, f.costo) for f in filas)
        return CompraIdempotente(compra=compra, total=compra.total, items=items)

    async def crear_compra(
        self,
        *,
        proveedor_id: int,
        fecha: datetime,
        items: list[ItemCompra],
        total: Decimal,
        usuario_id: int | None,
        idempotency_key: str | None = None,
    ) -> CompraLeer:
        """Inserta compra + detalle; por item suma stock (ENTRADA) y fija productos.precio_compra.

        `idempotency_key` se persiste con UNIQUE parcial (migración 0025): el chequeo previo del servicio
        evita el doble registro y el índice es el respaldo estructural ante una carrera.
        """
        compra = Compra(proveedor_id=proveedor_id, fecha=fecha, total=total,
                        idempotency_key=idempotency_key)
        self._s.add(compra)
        await self._s.flush()  # asigna compra.id

        for it in items:
            self._s.add(
                CompraDetalle(
                    compra_id=compra.id, producto_id=it.producto_id,
                    cantidad=it.cantidad, costo=it.costo,
                )
            )
            # Lock del producto ANTES de leer stock/promedio: serializa compras concurrentes del mismo
            # producto (sin lost update del promedio, ADR 0025). Orden de locks productos→inventario.
            promedio_actual = await self._lock_costo_promedio(it.producto_id)
            stock_prev = await self._sumar_stock(it.producto_id, it.cantidad)
            nuevo_promedio = _promedio_ponderado(
                stock_prev, promedio_actual, it.cantidad, it.costo
            )
            self._s.add(
                MovimientoInventario(
                    producto_id=it.producto_id, tipo="ENTRADA", cantidad=it.cantidad,
                    costo_unitario=it.costo, referencia=f"compra:{compra.id}", usuario_id=usuario_id,
                    fecha_operacion=fecha,
                )
            )
            # Fija el último costo de compra Y recalcula el promedio ponderado móvil (no toca el costo
            # ya snapshoteado en ventas pasadas).
            await self._s.execute(
                text(
                    "UPDATE productos SET precio_compra = :c, costo_promedio = :cp WHERE id = :p"
                ),
                {"c": it.costo, "cp": nuevo_promedio, "p": it.producto_id},
            )

        await self._s.flush()
        await publish(self._s, "compra_registrada", {
            "compra_id": compra.id, "proveedor_id": proveedor_id, "total": str(total),
        })
        await publish(self._s, "inventario_actualizado", {"compra_id": compra.id, "accion": "compra"})
        return await self._leer(compra.id)

    async def _lock_costo_promedio(self, producto_id: int) -> Decimal | None:
        """Bloquea la fila del producto (FOR UPDATE) y devuelve su `costo_promedio` actual (o NULL).

        El lock serializa las compras concurrentes del mismo producto: el promedio se lee y reescribe
        dentro de la sección crítica, sin lost update (ADR 0025)."""
        return (
            await self._s.execute(
                text("SELECT costo_promedio FROM productos WHERE id = :p FOR UPDATE"),
                {"p": producto_id},
            )
        ).scalar_one_or_none()

    async def _sumar_stock(self, producto_id: int, cantidad: Decimal) -> Decimal:
        """Suma `cantidad` al stock del producto (crea la fila si no existía). Devuelve el stock PREVIO.

        El stock previo (0 si la fila no existía) alimenta el promedio ponderado. Lee bajo FOR UPDATE
        para leer-modificar-escribir sin carreras."""
        existe = (
            await self._s.execute(
                select(Inventario.stock_actual)
                .where(Inventario.producto_id == producto_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existe is None:
            self._s.add(
                Inventario(producto_id=producto_id, stock_actual=cantidad, stock_minimo=Decimal("0"))
            )
            return Decimal("0")
        await self._s.execute(
            text("UPDATE inventario SET stock_actual = stock_actual + :c WHERE producto_id = :p"),
            {"c": cantidad, "p": producto_id},
        )
        return existe

    async def _leer(self, compra_id: int) -> CompraLeer:
        fila = (
            await self._s.execute(
                select(
                    Compra.id, Compra.proveedor_id,
                    Proveedor.nombre.label("proveedor_nombre"), Compra.fecha, Compra.total,
                )
                .join(Proveedor, Proveedor.id == Compra.proveedor_id, isouter=True)
                .where(Compra.id == compra_id)
            )
        ).one()
        return CompraLeer(
            id=fila.id, proveedor_id=fila.proveedor_id, proveedor_nombre=fila.proveedor_nombre,
            fecha=fila.fecha, total=Decimal(fila.total) if fila.total is not None else Decimal("0"),
        )

    async def listar(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> list[CompraLeer]:
        """Compras del rango (hora Colombia; el servicio resuelve el default mes), más reciente primero."""
        stmt = (
            select(
                Compra.id, Compra.proveedor_id,
                Proveedor.nombre.label("proveedor_nombre"), Compra.fecha, Compra.total,
            )
            .join(Proveedor, Proveedor.id == Compra.proveedor_id, isouter=True)
        )
        if inicio is not None:
            stmt = stmt.where(Compra.fecha >= inicio)
        if fin is not None:
            stmt = stmt.where(Compra.fecha <= fin)
        stmt = stmt.order_by(Compra.id.desc())
        filas = (await self._s.execute(stmt)).all()
        return [
            CompraLeer(
                id=f.id, proveedor_id=f.proveedor_id, proveedor_nombre=f.proveedor_nombre,
                fecha=f.fecha, total=Decimal(f.total) if f.total is not None else Decimal("0"),
            )
            for f in filas
        ]
