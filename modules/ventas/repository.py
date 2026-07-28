"""Repositorio de ventas: único lugar con SQL (regla no negociable #2).

Inserta venta + detalle + movimientos_inventario y descuenta stock en UNA transacción
(la sesión del tenant); el consecutivo sale de la SEQUENCE; emite el evento pg_notify.
El stock se bloquea con SELECT ... FOR UPDATE en lock_inventario (evita carreras).
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import column as sa_column
from sqlalchemy import delete, func, select
from sqlalchemy import table as sa_table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, selectinload

from core.config.timezone import now_co, rango_dia_co
from core.events import publish
from core.money import cuantizar
from modules.clientes.models import Cliente
from modules.facturacion.models import FacturaElectronica
from modules.inventario.busqueda import BuscadorProductos
from modules.inventario.models import Inventario, MovimientoInventario, Producto
from modules.inventario.precios import FraccionPrecio
from modules.inventario.repository import SqlInventarioRepository
from modules.ventas.models import Venta, VentaDetalle, VentaPago
from modules.ventas.schemas import (
    HistorialFeed,
    HistorialLinea,
    ItemVentaResumen,
    PagoParte,
    VentaConLineas,
    VentaDetalleLeer,
    VentaLeer,
    VentaRecienteLeer,
)
from modules.ventas.service import (
    EdicionVenta,
    FraccionBusqueda,
    ProductoBusqueda,
    ProductoPrecio,
    VentaHeader,
)

# Estados de factura electrónica que BLOQUEAN el borrado de la venta (factura "viva").
_ESTADOS_FACTURA_VIVA = ("pendiente", "aceptada")


# `usuarios` no tiene modelo ORM en este repo (patrón FK-less deliberado, igual que en proveedores y
# perfil): declararlo metería la tabla en el grafo de mappers de todos los módulos. Para resolver el
# nombre del vendedor alcanza una tabla literal.
_usuarios = sa_table("usuarios", sa_column("id"), sa_column("nombre"))


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

    async def listar_recientes(
        self, *, limite: int = 5, vendedor_id: int | None = None,
    ) -> list[VentaRecienteLeer]:
        """Últimas `limite` ventas COMPLETADAS de HOY (Colombia, fecha DESC) con sus items resueltos,
        para el feed del cockpit. Solo el día presente: el pulso del día no arrastra ventas de ayer.

        Dos queries (sin N+1): (1) las cabeceras recientes; (2) sus renglones en batch, uniendo a `productos`
        para el nombre de catálogo (COALESCE con la descripción de una venta varia). `vendedor_id` acota por
        RBAC (None = todas). Excluye anuladas: el pulso del día no debe mostrar ventas revertidas."""
        inicio, fin = rango_dia_co()
        cab_stmt = (
            select(Venta.id, Venta.consecutivo, Venta.fecha, Venta.total, Venta.metodo_pago)
            .where(Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin)
        )
        if vendedor_id is not None:
            cab_stmt = cab_stmt.where(Venta.vendedor_id == vendedor_id)
        cab_stmt = cab_stmt.order_by(Venta.fecha.desc()).limit(limite)
        cabeceras = (await self._s.execute(cab_stmt)).all()
        if not cabeceras:
            return []

        ids = [c.id for c in cabeceras]
        items_stmt = (
            select(
                VentaDetalle.venta_id,
                VentaDetalle.cantidad,
                func.coalesce(Producto.nombre, VentaDetalle.descripcion, "Producto").label("nombre"),
            )
            .select_from(VentaDetalle)
            .outerjoin(Producto, Producto.id == VentaDetalle.producto_id)
            .where(VentaDetalle.venta_id.in_(ids))
            .order_by(VentaDetalle.venta_id, VentaDetalle.id)
        )
        por_venta: dict[int, list[ItemVentaResumen]] = {}
        for fila in (await self._s.execute(items_stmt)).all():
            por_venta.setdefault(fila.venta_id, []).append(
                ItemVentaResumen(nombre=fila.nombre, cantidad=fila.cantidad)
            )
        return [
            VentaRecienteLeer(
                id=c.id, consecutivo=c.consecutivo, fecha=c.fecha, total=c.total,
                metodo_pago=c.metodo_pago, items=por_venta.get(c.id, []),
                num_items=len(por_venta.get(c.id, [])),
            )
            for c in cabeceras
        ]

    async def historial_lineas(
        self, *, desde: date, hasta: date, vendedor_id: int | None = None,
        limite: int = 100, offset: int = 0,
    ) -> HistorialFeed:
        """El libro de ventas del rango: una fila por RENGLÓN vendido, con producto, cliente y
        vendedor ya resueltos.

        **Pagina por VENTA, no por renglón.** `limite` cuenta ventas: así un grupo nunca queda
        partido entre dos páginas y el front no tiene que reconstruir grupos a caballo. `hay_mas`
        sale de pedir una venta de más, no de un `COUNT(*)` sobre todo el rango.

        Sin N+1: dos consultas (ids + renglones en batch), y una tercera SOLO si alguna de esas
        ventas fue mixta. El nombre de producto se resuelve con el mismo `coalesce` de
        `listar_recientes`; el cliente ausente cae en 'Consumidor Final' en SQL, no en el front.

        Incluye las ANULADAS, marcadas en `estado`: esto es un libro, y lo que se anuló también es
        historia. Ojo con la asimetría deliberada: `/reportes/resumen` sí las excluye de los totales.

        El día es el colombiano (`rango_dia_co` da instantes aware): nunca un `::date` crudo, que
        depende del `TimeZone` de la sesión de Postgres y corre las ventas de la noche al día
        siguiente.
        """
        inicio, fin = rango_dia_co(desde, hasta)
        ids_stmt = (
            select(Venta.id)
            .where(Venta.fecha >= inicio, Venta.fecha <= fin)
            .order_by(Venta.fecha.desc(), Venta.id.desc())
            .limit(limite + 1)
            .offset(offset)
        )
        if vendedor_id is not None:
            ids_stmt = ids_stmt.where(Venta.vendedor_id == vendedor_id)
        ids = [f.id for f in (await self._s.execute(ids_stmt)).all()]
        hay_mas = len(ids) > limite
        ids = ids[:limite]
        if not ids:
            return HistorialFeed(desde=desde, hasta=hasta, filas=[], hay_mas=False)

        lineas_stmt = (
            select(
                VentaDetalle.id.label("linea_id"),
                Venta.id.label("venta_id"), Venta.consecutivo, Venta.fecha, Venta.estado,
                Venta.metodo_pago, Venta.total.label("venta_total"),
                Venta.cliente_id, Venta.vendedor_id,
                VentaDetalle.producto_id, VentaDetalle.cantidad,
                VentaDetalle.precio_unitario, VentaDetalle.iva,
                func.coalesce(Producto.nombre, VentaDetalle.descripcion, "Producto").label("producto"),
                func.coalesce(Cliente.nombre, "Consumidor Final").label("cliente"),
                func.coalesce(_usuarios.c.nombre, "—").label("vendedor"),
            )
            .select_from(VentaDetalle)
            .join(Venta, Venta.id == VentaDetalle.venta_id)
            .outerjoin(Producto, Producto.id == VentaDetalle.producto_id)
            .outerjoin(Cliente, Cliente.id == Venta.cliente_id)
            .outerjoin(_usuarios, _usuarios.c.id == Venta.vendedor_id)
            .where(VentaDetalle.venta_id.in_(ids))
            .order_by(Venta.fecha.desc(), Venta.id.desc(), VentaDetalle.id)
        )
        filas = (await self._s.execute(lineas_stmt)).all()

        # Las partes de una venta MIXTA, solo si hay alguna: `metodo_pago='mixto'` no es un método de
        # pago, es un marcador, y sin el desglose la columna Método del historial mentiría.
        ids_mixtas = {f.venta_id for f in filas if f.metodo_pago == "mixto"}
        pagos_por_venta: dict[int, list[PagoParte]] = {}
        if ids_mixtas:
            pagos_stmt = (
                select(VentaPago.venta_id, VentaPago.metodo, VentaPago.monto)
                .where(VentaPago.venta_id.in_(ids_mixtas))
                .order_by(VentaPago.venta_id, VentaPago.id)
            )
            for p in (await self._s.execute(pagos_stmt)).all():
                pagos_por_venta.setdefault(p.venta_id, []).append(
                    PagoParte(metodo=p.metodo, monto=p.monto)
                )

        num_lineas: dict[int, int] = {}
        for f in filas:
            num_lineas[f.venta_id] = num_lineas.get(f.venta_id, 0) + 1

        return HistorialFeed(
            desde=desde, hasta=hasta, hay_mas=hay_mas,
            filas=[
                HistorialLinea(
                    linea_id=f.linea_id, venta_id=f.venta_id, consecutivo=f.consecutivo,
                    fecha=f.fecha, estado=f.estado, producto=f.producto,
                    producto_id=f.producto_id, cantidad=f.cantidad,
                    precio_unitario=f.precio_unitario, iva=f.iva,
                    # Se reconstruye como la factura DIAN: cantidad x precio. NO se suma para
                    # obtener totales — para eso está `venta_total` (ver el docstring del schema).
                    total_linea=cuantizar(f.cantidad * f.precio_unitario),
                    cliente=f.cliente, cliente_id=f.cliente_id,
                    vendedor=f.vendedor, vendedor_id=f.vendedor_id,
                    metodo_pago=f.metodo_pago, pagos=pagos_por_venta.get(f.venta_id, []),
                    venta_total=f.venta_total, num_lineas=num_lineas[f.venta_id],
                )
                for f in filas
            ],
        )

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

    async def tiene_devolucion(self, venta_id: int) -> bool:
        """¿La venta tiene una devolución registrada? (ADR 0026: bloquea borrar/editar con 409 legible.)

        Lectura cross-módulo a `devoluciones` con SQL de texto (mismo criterio que las FKs planas: no
        acoplar el grafo de mappers entre módulos). Sin este guard, el DELETE moriría en la FK
        `devoluciones.venta_id` con un 500 opaco.
        """
        fila = (
            await self._s.execute(
                text("SELECT id FROM devoluciones WHERE venta_id=:v LIMIT 1"), {"v": venta_id}
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
            inv = self._inventario_para_descontar(ln.producto_id)
            inv.stock_actual = inv.stock_actual - ln.cantidad
            self._s.add(MovimientoInventario(
                producto_id=ln.producto_id, tipo="SALIDA", cantidad=ln.cantidad,
                costo_unitario=ln.costo_unitario, referencia=f"venta:{venta.id}",
                usuario_id=venta.vendedor_id, fecha_operacion=venta.fecha,
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
            costo_promedio=prod.costo_promedio,
            precio_umbral=prod.precio_umbral,
            precio_bajo_umbral=prod.precio_bajo_umbral,
            precio_sobre_umbral=prod.precio_sobre_umbral,
            fracciones=fracciones,
            unidad_medida=prod.unidad_medida,
            tipo_impuesto=prod.tipo_impuesto,
            contenido_paquete=prod.contenido_paquete,
            precio_paquete=prod.precio_paquete,
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

    def _inventario_para_descontar(self, producto_id: int) -> Inventario:
        """Fila de inventario bloqueada por `lock_inventario`; si el producto no la tiene (datos
        migrados por ETL), se crea al vuelo en cero — el descuento la deja en negativo honesto
        (modo permisivo) y el movimiento SALIDA se registra igual (regla #7)."""
        inv = self._locked.get(producto_id)
        if inv is None:
            inv = Inventario(producto_id=producto_id, stock_actual=Decimal("0"), stock_minimo=Decimal("0"))
            self._s.add(inv)
            self._locked[producto_id] = inv
        return inv

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

    async def registrar_alias(
        self, termino: str, reemplazo: str, *, producto_id: int | None = None
    ) -> bool:
        """Upsert de un alias de búsqueda (variante/typo → término canónico). Devuelve True si se
        creó, False si ya existía y se actualizó el reemplazo. Dedup por `termino` (UNIQUE)."""
        fila = (await self._s.execute(
            text(
                "INSERT INTO aliases (termino, reemplazo, producto_id) "
                "VALUES (:t, :r, :pid) "
                "ON CONFLICT (termino) DO UPDATE SET reemplazo = EXCLUDED.reemplazo, "
                "producto_id = EXCLUDED.producto_id, actualizado_en = now() "
                "RETURNING (xmax = 0) AS creado"
            ),
            {"t": termino, "r": reemplazo, "pid": producto_id},
        )).scalar_one()
        return bool(fila)

    async def pagos_de_venta(self, venta_id: int) -> list[tuple[str, Decimal]]:
        """Partes del cobro de una venta mixta [(metodo, monto)]; lista vacía en las normales."""
        filas = (
            await self._s.execute(
                select(VentaPago.metodo, VentaPago.monto).where(VentaPago.venta_id == venta_id)
            )
        ).all()
        return [(f.metodo, Decimal(f.monto)) for f in filas]

    async def reemplazar_pagos(self, venta_id: int, pagos: list[PagoParte]) -> None:
        """Reemplaza el desglose de cobro de una venta (edición): borra las partes viejas e inserta
        las nuevas (ninguna si la venta dejó de ser mixta). Misma transacción que la edición."""
        await self._s.execute(delete(VentaPago).where(VentaPago.venta_id == venta_id))
        for pago in pagos:
            self._s.add(VentaPago(venta_id=venta_id, metodo=pago.metodo, monto=pago.monto))
        await self._s.flush()

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
                tipo_impuesto=getattr(ln, "tipo_impuesto", None) or "iva",
            ))
        self._s.add(venta)
        await self._s.flush()  # asigna venta.id

        # Partes del cobro mixto (F5/0053): misma transacción que la venta. Las normales no escriben.
        for pago in header.pagos:
            self._s.add(VentaPago(venta_id=venta.id, metodo=pago.metodo, monto=pago.monto))

        for ln in header.lineas:
            if not ln.descontar_stock or ln.producto_id is None:
                continue
            inv = self._inventario_para_descontar(ln.producto_id)
            inv.stock_actual = inv.stock_actual - ln.cantidad
            self._s.add(MovimientoInventario(
                producto_id=ln.producto_id, tipo="SALIDA", cantidad=ln.cantidad,
                costo_unitario=ln.costo_unitario, referencia=f"venta:{venta.id}",
                usuario_id=header.vendedor_id, fecha_operacion=venta.fecha,
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
