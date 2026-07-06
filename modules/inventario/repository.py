"""Repositorio de inventario: único lugar con SQL del módulo (regla no negociable #2).

Cubre lecturas de catálogo/stock/kardex, el ajuste transaccional (lock + movimiento + evento)
y las 4 capas de búsqueda (exacta, alias, trigram pg_trgm, candidatos para fuzzy).
"""
from decimal import Decimal

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.inventario.busqueda import AliasResuelto
from modules.inventario.models import (
    Inventario,
    MovimientoInventario,
    Producto,
    ProductoFraccion,
)
from modules.inventario.schemas import ProductoActualizar, ProductoCrear
from modules.compras.models import Proveedor


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

    async def ids_frecuentes(self, *, dias: int, limite: int) -> list[int]:
        """IDs de los productos MÁS VENDIDOS en los últimos `dias` (por nº de líneas), activos.

        Alimenta la grilla de acceso rápido de Ventas Rápidas. Cuenta líneas de venta (no cantidad):
        lo que más se toca en el mostrador, no lo de mayor volumen. Vacío si aún no hay ventas."""
        filas = (await self._s.execute(
            text(
                "SELECT d.producto_id FROM ventas_detalle d "
                "JOIN ventas v ON v.id = d.venta_id "
                "JOIN productos p ON p.id = d.producto_id "
                "WHERE d.producto_id IS NOT NULL AND p.activo "
                "AND v.fecha >= now() - make_interval(days => :dias) "
                "GROUP BY d.producto_id ORDER BY count(*) DESC LIMIT :limite"
            ),
            {"dias": dias, "limite": limite},
        )).scalars().all()
        return list(filas)

    # ---- Catálogo (mutaciones) ----------------------------------------------
    async def codigo_existe(self, codigo: str, *, excluir_id: int | None = None) -> bool:
        """¿Otro producto ya usa este código? (`excluir_id` se ignora a sí mismo al editar)."""
        stmt = select(Producto.id).where(Producto.codigo == codigo)
        if excluir_id is not None:
            stmt = stmt.where(Producto.id != excluir_id)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def proveedor_existe(self, proveedor_id: int) -> bool:
        """¿El `proveedor_id` corresponde a un proveedor registrado? (valida la FK antes de insertar)."""
        return (
            await self._s.execute(select(Proveedor.id).where(Proveedor.id == proveedor_id).limit(1))
        ).first() is not None

    async def categorias_distinct(self) -> list[str]:
        """Categorías existentes (DISTINCT, no nulas, ordenadas) para el select de categoría."""
        rows = (
            await self._s.execute(
                select(Producto.categoria)
                .where(Producto.categoria.is_not(None))
                .distinct()
                .order_by(Producto.categoria)
            )
        ).scalars().all()
        return list(rows)

    async def _cargar_proveedor(self, producto: Producto, proveedor_id: int | None) -> None:
        """Fija la relación `proveedor` para la respuesta (evita un lazy-load async tras mutar)."""
        producto.proveedor = (
            await self._s.get(Proveedor, proveedor_id) if proveedor_id is not None else None
        )

    async def crear_producto(self, datos: ProductoCrear, *, usuario_id: int | None) -> Producto:
        """Inserta el producto, sus fracciones y su fila de inventario (stock 0) en la misma transacción.

        El inventario nace en 0 (stock_actual y stock_minimo) SIN movimiento: el stock real lo fija el
        conteo físico después. `usuario_id` se mantiene por compatibilidad de firma (ya no hay ENTRADA).
        Emite el evento al final.
        """
        producto = Producto(
            codigo=datos.codigo, nombre=datos.nombre, categoria=datos.categoria,
            proveedor_id=datos.proveedor_id, unidad_medida=datos.unidad_medida,
            precio_venta=datos.precio_venta, precio_compra=datos.precio_compra,
            precio_especial=datos.precio_especial, precio_umbral=datos.precio_umbral,
            precio_bajo_umbral=datos.precio_bajo_umbral, precio_sobre_umbral=datos.precio_sobre_umbral,
            iva=datos.iva, permite_fraccion=datos.permite_fraccion, activo=datos.activo,
            fracciones=[
                ProductoFraccion(
                    fraccion=fr.fraccion, decimal=fr.decimal,
                    precio_total=fr.precio_total, precio_unitario=fr.precio_unitario,
                )
                for fr in datos.fracciones
            ],
        )
        self._s.add(producto)
        await self._s.flush()  # asigna producto.id

        self._s.add(
            Inventario(
                producto_id=producto.id, stock_actual=Decimal("0"), stock_minimo=Decimal("0"),
            )
        )
        await self._s.flush()
        await self._cargar_proveedor(producto, datos.proveedor_id)
        await publish(self._s, "inventario_actualizado", {
            "producto_id": producto.id, "accion": "creado",
        })
        return producto

    async def actualizar_producto(
        self, producto_id: int, datos: ProductoActualizar
    ) -> Producto | None:
        """Actualiza los campos del producto y REEMPLAZA sus fracciones (cascade delete-orphan).

        No toca el inventario (`stock_actual`/`stock_minimo` van por el ajuste/conteo). Devuelve None si
        el producto no existe. Emite el evento al final.
        """
        producto = (
            await self._s.execute(select(Producto).where(Producto.id == producto_id))
        ).scalar_one_or_none()
        if producto is None:
            return None

        producto.codigo = datos.codigo
        producto.nombre = datos.nombre
        producto.categoria = datos.categoria
        producto.proveedor_id = datos.proveedor_id
        producto.unidad_medida = datos.unidad_medida
        producto.precio_venta = datos.precio_venta
        producto.precio_compra = datos.precio_compra
        producto.precio_especial = datos.precio_especial
        producto.precio_umbral = datos.precio_umbral
        producto.precio_bajo_umbral = datos.precio_bajo_umbral
        producto.precio_sobre_umbral = datos.precio_sobre_umbral
        producto.iva = datos.iva
        producto.permite_fraccion = datos.permite_fraccion
        producto.activo = datos.activo
        # Reasignar la colección (cargada por selectin) borra las fracciones huérfanas en el flush.
        producto.fracciones = [
            ProductoFraccion(
                fraccion=fr.fraccion, decimal=fr.decimal,
                precio_total=fr.precio_total, precio_unitario=fr.precio_unitario,
            )
            for fr in datos.fracciones
        ]
        await self._s.flush()
        await self._cargar_proveedor(producto, datos.proveedor_id)
        await publish(self._s, "inventario_actualizado", {
            "producto_id": producto_id, "accion": "editado",
        })
        return producto

    async def soft_delete_producto(self, producto_id: int) -> bool:
        """Marca el producto como inactivo (`activo=false`); nunca hard-delete (lo referencian ventas).

        Devuelve False si no existe. Emite el evento al final.
        """
        producto = (
            await self._s.execute(select(Producto).where(Producto.id == producto_id))
        ).scalar_one_or_none()
        if producto is None:
            return False
        producto.activo = False
        await self._s.flush()
        await publish(self._s, "inventario_actualizado", {
            "producto_id": producto_id, "accion": "eliminado",
        })
        return True

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
    async def ajuste_por_key(self, idempotency_key: str) -> MovimientoInventario | None:
        """Idempotencia estructural: el movimiento ya registrado con esta key, o None."""
        return (
            await self._s.execute(
                select(MovimientoInventario)
                .where(MovimientoInventario.idempotency_key == idempotency_key)
                .limit(1)
            )
        ).scalar_one_or_none()

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
        referencia: str | None,
        usuario_id: int | None,
        idempotency_key: str | None = None,
    ) -> int:
        """Actualiza inventario (o lo crea), inserta el AJUSTE y emite el evento en la misma tx.

        Devuelve el id del movimiento. El evento se emite solo aquí (primer apply); el replay
        lo resuelve el servicio sin llamar a este método.
        """
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
        movimiento = MovimientoInventario(
            producto_id=producto_id, tipo="AJUSTE", cantidad=delta,
            referencia=referencia, usuario_id=usuario_id, idempotency_key=idempotency_key,
        )
        self._s.add(movimiento)
        await self._s.flush()
        await publish(self._s, "inventario_actualizado", {
            "producto_id": producto_id,
            "stock_actual": str(nuevo_stock),
            "tipo": "AJUSTE",
        })
        return movimiento.id

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
