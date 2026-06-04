"""Repositorio de inventario: único lugar con SQL del módulo (regla no negociable #2).

Cubre lecturas de catálogo/stock/kardex, el ajuste transaccional (lock + movimiento + evento)
y las 4 capas de búsqueda (exacta, alias, trigram pg_trgm, candidatos para fuzzy).
"""
from decimal import Decimal

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.inventario.busqueda import AliasResuelto
from modules.inventario.models import Inventario, MovimientoInventario, Producto


class SqlInventarioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- Catálogo (lecturas) -------------------------------------------------
    async def listar_productos(
        self,
        *,
        categoria: str | None = None,
        activo: bool | None = None,
        ids: list[int] | None = None,
        limite: int = 50,
        offset: int = 0,
    ) -> list[Producto]:
        stmt: Select = select(Producto)
        if ids is not None:
            stmt = stmt.where(Producto.id.in_(ids))
        if categoria is not None:
            stmt = stmt.where(Producto.categoria == categoria)
        if activo is not None:
            stmt = stmt.where(Producto.activo.is_(activo))
        stmt = stmt.order_by(Producto.nombre).limit(limite).offset(offset)
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener_producto(self, producto_id: int) -> Producto | None:
        return (
            await self._s.execute(select(Producto).where(Producto.id == producto_id))
        ).scalar_one_or_none()

    async def listar_stock(
        self, *, solo_bajo: bool = False, limite: int = 100, offset: int = 0
    ) -> list[dict]:
        bajo_expr = Inventario.stock_actual < Inventario.stock_minimo
        stmt = (
            select(
                Inventario.producto_id,
                Producto.nombre,
                Inventario.stock_actual,
                Inventario.stock_minimo,
                bajo_expr.label("bajo"),
            )
            .join(Producto, Producto.id == Inventario.producto_id)
            .order_by(Producto.nombre)
            .limit(limite)
            .offset(offset)
        )
        if solo_bajo:
            stmt = stmt.where(bajo_expr)
        return [dict(row._mapping) for row in (await self._s.execute(stmt)).all()]

    async def kardex(
        self, producto_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[MovimientoInventario]:
        stmt = (
            select(MovimientoInventario)
            .where(MovimientoInventario.producto_id == producto_id)
            .order_by(MovimientoInventario.creado_en.desc(), MovimientoInventario.id.desc())
            .limit(limite)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    # ---- Ajuste (transaccional) ---------------------------------------------
    async def ajuste_existente(self, referencia: str) -> Decimal | None:
        """Idempotencia del ajuste: si ya hay un AJUSTE con esta referencia, devuelve el stock."""
        existe = (
            await self._s.execute(
                select(MovimientoInventario.producto_id)
                .where(MovimientoInventario.tipo == "AJUSTE")
                .where(MovimientoInventario.referencia == referencia)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existe is None:
            return None
        return await self.stock_actual(existe)

    async def lock_stock(self, producto_id: int) -> Decimal | None:
        return (
            await self._s.execute(
                select(Inventario.stock_actual)
                .where(Inventario.producto_id == producto_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def stock_actual(self, producto_id: int) -> Decimal | None:
        return (
            await self._s.execute(
                select(Inventario.stock_actual).where(Inventario.producto_id == producto_id)
            )
        ).scalar_one_or_none()

    async def aplicar_ajuste(
        self,
        *,
        producto_id: int,
        delta: Decimal,
        nuevo_stock: Decimal,
        referencia: str,
        usuario_id: int | None,
    ) -> None:
        """Actualiza inventario (o lo crea) e inserta el movimiento AJUSTE en la misma tx."""
        existe = await self.stock_actual(producto_id)
        if existe is None:
            self._s.add(
                Inventario(producto_id=producto_id, stock_actual=nuevo_stock, stock_minimo=Decimal("0"))
            )
        else:
            await self._s.execute(
                text("UPDATE inventario SET stock_actual = :s WHERE producto_id = :p"),
                {"s": nuevo_stock, "p": producto_id},
            )
        self._s.add(
            MovimientoInventario(
                producto_id=producto_id, tipo="AJUSTE", cantidad=delta,
                referencia=referencia, usuario_id=usuario_id,
            )
        )
        await self._s.flush()
        await publish(self._s, "inventario_actualizado", {
            "producto_id": producto_id,
            "stock_actual": str(nuevo_stock),
            "tipo": "AJUSTE",
        })

    # ---- Búsqueda (4 capas; implementa BusquedaRepo) ------------------------
    async def buscar_exacta(self, query: str, limite: int) -> list[tuple[int, str]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, nombre FROM productos "
                    "WHERE activo AND lower(btrim(nombre)) = lower(btrim(:q)) "
                    "ORDER BY nombre LIMIT :lim"
                ),
                {"q": query, "lim": limite},
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    async def buscar_alias(self, query: str) -> AliasResuelto | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT a.reemplazo, a.producto_id, p.nombre "
                    "FROM aliases a LEFT JOIN productos p ON p.id = a.producto_id "
                    "WHERE lower(btrim(a.termino)) = lower(btrim(:q)) LIMIT 1"
                ),
                {"q": query},
            )
        ).first()
        if row is None:
            return None
        return AliasResuelto(
            termino=query, reemplazo=row[0], producto_id=row[1], nombre_producto=row[2]
        )

    async def buscar_trigram(
        self, query: str, umbral: float, limite: int
    ) -> list[tuple[int, str, float]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, nombre, similarity(nombre, :q) AS sim FROM productos "
                    "WHERE activo AND similarity(nombre, :q) >= :umbral "
                    "ORDER BY sim DESC, nombre LIMIT :lim"
                ),
                {"q": query, "umbral": umbral, "lim": limite},
            )
        ).all()
        return [(r[0], r[1], float(r[2])) for r in rows]

    async def nombres_activos(self) -> list[tuple[int, str]]:
        rows = (
            await self._s.execute(
                text("SELECT id, nombre FROM productos WHERE activo")
            )
        ).all()
        return [(r[0], r[1]) for r in rows]
