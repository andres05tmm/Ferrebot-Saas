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
    """Una línea ya validada (producto, cantidad, costo) lista para persistir.

    `producto_id` es opcional: las compras del vertical construcción imputadas a obra o de viaje de
    material no llevan producto de catálogo (no mueven stock). En una compra de catálogo el servicio
    garantiza que viene (lo valida el schema).
    """

    producto_id: int | None
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
    items: tuple[tuple[int | None, Decimal, Decimal], ...]


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
        obra_id: int | None = None,
        categoria: str | None = None,
        es_viaje_material: bool = False,
        precio_venta_cliente: Decimal | None = None,
        resbalo: Decimal | None = None,
        factura_url: str | None = None,
    ) -> CompraLeer:
        """Inserta compra + detalle. Por item suma stock (ENTRADA) y fija productos.precio_compra SOLO
        en la compra de catálogo.

        INVARIANTE (spec 11 / plan PIM §2): la compra imputada a OBRA (`obra_id`) o de VIAJE DE MATERIAL
        (`es_viaje_material`) NO mueve stock — solo registra la compra y su detalle como gasto imputado,
        sin `movimientos_inventario` ni `inventario`/`precio_compra`. La de catálogo (sin obra, sin viaje)
        SIGUE moviendo stock como hoy (regla #7). El discriminador es una sola verdad: `mueve_stock`.

        `idempotency_key` se persiste con UNIQUE parcial (migración 0025): el chequeo previo del servicio
        evita el doble registro y el índice es el respaldo estructural ante una carrera.
        """
        mueve_stock = obra_id is None and not es_viaje_material
        compra = Compra(
            proveedor_id=proveedor_id, fecha=fecha, total=total, idempotency_key=idempotency_key,
            obra_id=obra_id, categoria=categoria, es_viaje_material=es_viaje_material,
            precio_venta_cliente=precio_venta_cliente, resbalo=resbalo, factura_url=factura_url,
        )
        self._s.add(compra)
        await self._s.flush()  # asigna compra.id

        for it in items:
            # El detalle SIEMPRE se registra (deja constancia de la línea, mueva stock o no).
            self._s.add(
                CompraDetalle(
                    compra_id=compra.id, producto_id=it.producto_id,
                    cantidad=it.cantidad, costo=it.costo,
                )
            )
            if not mueve_stock:
                # Imputada a obra/viaje: solo imputa, no toca inventario (invariante "nada mueve stock
                # sin movimiento" — aquí no hay movimiento porque no hay entrada al catálogo).
                continue
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
        if mueve_stock:
            # Solo la compra de catálogo cambió el inventario: solo ella emite el evento.
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

    # Columnas de la cabecera (incluye las del vertical construcción). Una sola verdad para _leer/listar.
    _COLS_CABECERA = (
        Compra.id, Compra.proveedor_id, Proveedor.nombre.label("proveedor_nombre"),
        Compra.fecha, Compra.total, Compra.obra_id, Compra.categoria, Compra.es_viaje_material,
        Compra.precio_venta_cliente, Compra.resbalo, Compra.factura_url,
    )

    @staticmethod
    def _fila_a_leer(f) -> CompraLeer:
        """Mapea una fila de la cabecera a `CompraLeer`. `mueve_stock` es derivado (obra/viaje → False);
        los `resbalo_pct`/`resbalo_alerta`/`alerta_precio_proveedor` los completa el servicio."""
        return CompraLeer(
            id=f.id, proveedor_id=f.proveedor_id, proveedor_nombre=f.proveedor_nombre,
            fecha=f.fecha, total=Decimal(f.total) if f.total is not None else Decimal("0"),
            obra_id=f.obra_id, categoria=f.categoria, es_viaje_material=f.es_viaje_material,
            precio_venta_cliente=f.precio_venta_cliente, resbalo=f.resbalo, factura_url=f.factura_url,
            mueve_stock=(f.obra_id is None and not f.es_viaje_material),
        )

    async def _leer(self, compra_id: int) -> CompraLeer:
        fila = (
            await self._s.execute(
                select(*self._COLS_CABECERA)
                .join(Proveedor, Proveedor.id == Compra.proveedor_id, isouter=True)
                .where(Compra.id == compra_id)
            )
        ).one()
        return self._fila_a_leer(fila)

    async def listar(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> list[CompraLeer]:
        """Compras del rango (hora Colombia; el servicio resuelve el default mes), más reciente primero."""
        stmt = (
            select(*self._COLS_CABECERA)
            .join(Proveedor, Proveedor.id == Compra.proveedor_id, isouter=True)
        )
        if inicio is not None:
            stmt = stmt.where(Compra.fecha >= inicio)
        if fin is not None:
            stmt = stmt.where(Compra.fecha <= fin)
        stmt = stmt.order_by(Compra.id.desc())
        filas = (await self._s.execute(stmt)).all()
        return [self._fila_a_leer(f) for f in filas]

    async def listar_resbalos(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> list[CompraLeer]:
        """Viajes de material del rango (reporte de resbalos, spec 11), del mayor margen al menor.

        Solo `es_viaje_material` (los que generan margen); el pct/alerta los deriva el servicio con la
        función pura `calcular_resbalo` para no meter lógica de dominio en el SQL."""
        stmt = (
            select(*self._COLS_CABECERA)
            .join(Proveedor, Proveedor.id == Compra.proveedor_id, isouter=True)
            .where(Compra.es_viaje_material.is_(True))
        )
        if inicio is not None:
            stmt = stmt.where(Compra.fecha >= inicio)
        if fin is not None:
            stmt = stmt.where(Compra.fecha <= fin)
        stmt = stmt.order_by(Compra.resbalo.desc().nullslast(), Compra.id.desc())
        filas = (await self._s.execute(stmt)).all()
        return [self._fila_a_leer(f) for f in filas]

    async def promedio_costo_unitario_proveedor(
        self,
        proveedor_id: int,
        *,
        desde: datetime,
        hasta: datetime,
        categoria: str | None = None,
    ) -> Decimal | None:
        """Promedio del costo unitario de las compras del proveedor en [desde, hasta) — None si no hay
        historial. Filtra por categoría cuando se da (compara peras con peras: asfalto vs asfalto).

        Alimenta la alerta de precio (>15% sobre el promedio de 6 meses, spec 10). Se llama ANTES de
        insertar la compra nueva, así que la ventana no la incluye."""
        stmt = (
            select(func.avg(CompraDetalle.costo))
            .join(Compra, Compra.id == CompraDetalle.compra_id)
            .where(
                Compra.proveedor_id == proveedor_id,
                Compra.fecha >= desde,
                Compra.fecha < hasta,
                CompraDetalle.costo.isnot(None),
            )
        )
        if categoria is not None:
            stmt = stmt.where(Compra.categoria == categoria)
        promedio = (await self._s.execute(stmt)).scalar_one_or_none()
        return Decimal(promedio) if promedio is not None else None
