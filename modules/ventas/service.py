"""Servicio de ventas: lógica de dominio pura y testeable (sin SQL directo).

Depende del protocolo `VentasRepo`; los tests unitarios inyectan un repo falso. Calcula
totales con IVA INCLUIDO en el precio (estándar retail Colombia): el precio del catálogo es
la fuente de verdad (ferrebot-logica-portar.md §1). El consecutivo sale de una SEQUENCE.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from core.config.timezone import rango_dia_co
from core.money import cuantizar as _money
from modules.inventario.precios import (
    EsquemaPrecio,
    FraccionPrecio,
    obtener_precio_para_cantidad,
)
from modules.ventas.errors import (
    LineaInvalida,
    OperacionNoAutorizada,
    ProductoNoEncontrado,
    StockInsuficiente,
    VentaConFacturaViva,
    VentaNoEncontrada,
    VentaNoEsDeHoy,
)
from modules.ventas.schemas import VentaConLineas, VentaCrear, VentaLeer


def es_de_hoy_co(fecha: datetime) -> bool:
    """¿El instante `fecha` cae dentro del día de HOY en hora Colombia? (función pura)."""
    inicio, fin = rango_dia_co()
    return inicio <= fecha <= fin


@dataclass(frozen=True, slots=True)
class ProductoPrecio:
    id: int
    nombre: str
    precio_venta: Decimal
    iva: int
    activo: bool
    # Costo de compra AL MOMENTO de vender: se hila hasta el movimiento SALIDA (costo de ventas exacto).
    precio_compra: Decimal | None = None
    precio_umbral: Decimal | None = None
    precio_bajo_umbral: Decimal | None = None
    precio_sobre_umbral: Decimal | None = None
    fracciones: tuple[FraccionPrecio, ...] = field(default_factory=tuple)
    # Unidad de venta del catálogo: "Unidad" (default) o sub-unidad de granel ("GRM"/"Cms") que el
    # motor de precios usa para cobrar por gramo/cm (puntillas, lija esmeril). Ver precios.py.
    unidad_medida: str = "Unidad"

    def esquema(self) -> EsquemaPrecio:
        """Arma el esquema que consume el motor de precios (modules.inventario)."""
        return EsquemaPrecio(
            precio_venta=self.precio_venta,
            precio_umbral=self.precio_umbral,
            precio_bajo_umbral=self.precio_bajo_umbral,
            precio_sobre_umbral=self.precio_sobre_umbral,
            fracciones=self.fracciones,
            unidad_medida=self.unidad_medida,
        )


@dataclass(frozen=True, slots=True)
class FraccionBusqueda:
    """Fracción disponible de un producto para la consulta: etiqueta de texto + precio total.

    A diferencia de `FraccionPrecio` (decimal + precio_total, para el motor de precios), aquí importa
    la ETIQUETA tal como está en `productos_fracciones.fraccion` (p. ej. "1/2") para mostrarla al usuario.
    """

    etiqueta: str
    precio_total: Decimal


@dataclass(frozen=True, slots=True)
class ProductoBusqueda:
    """Coincidencia de búsqueda de catálogo para una consulta de SOLO LECTURA (consultar_producto).

    No se reusa `ProductoPrecio`: ese modela el esquema de PRECIOS (umbral/fracciones/iva) y NO lleva
    stock. La consulta necesita precio base, stock, la unidad de empaque y las fracciones (con su
    etiqueta) para que el modelo responda cualquier fracción desde una sola consulta.
    """

    id: int
    nombre: str
    precio: Decimal
    stock: Decimal
    unidad_medida: str = "Unidad"
    fracciones: tuple[FraccionBusqueda, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LineaResuelta:
    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    iva: int
    total_linea: Decimal
    descontar_stock: bool
    # Costo del producto al vender (None en varia: no hay mercancía → sin movimiento ni costo).
    costo_unitario: Decimal | None = None


@dataclass(frozen=True, slots=True)
class VentaHeader:
    consecutivo: int
    cliente_id: int | None
    vendedor_id: int
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    metodo_pago: str
    origen: str
    idempotency_key: str | None
    lineas: list[LineaResuelta] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EdicionVenta:
    """Cabecera + líneas resueltas de una edición en el lugar (mantiene id/consecutivo/fecha)."""

    cliente_id: int | None
    metodo_pago: str
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    lineas: list[LineaResuelta] = field(default_factory=list)


class VentasRepo(Protocol):
    """Puerto de datos de ventas (lo implementa SqlVentasRepository; los tests lo falsean)."""

    async def buscar_por_idempotency(self, key: str) -> VentaLeer | None: ...
    async def obtener_producto(self, producto_id: int) -> ProductoPrecio | None: ...
    async def lock_inventario(self, producto_id: int) -> Decimal | None: ...
    async def obtener_producto_busqueda(self, producto_id: int) -> "ProductoBusqueda | None": ...
    async def buscar_productos_por_nombre(self, texto: str) -> list[tuple[int, str]]: ...
    async def listar(
        self, *, desde: date | None = None, hasta: date | None = None, vendedor_id: int | None = None
    ) -> list[VentaLeer]: ...
    async def siguiente_consecutivo(self) -> int: ...
    async def crear_venta(self, header: VentaHeader) -> VentaLeer: ...
    async def obtener_cabecera(self, venta_id: int) -> VentaLeer | None: ...
    async def tiene_factura_viva(self, venta_id: int) -> bool: ...
    async def borrar_venta(self, venta_id: int) -> None: ...
    async def revertir_lineas(self, venta_id: int) -> None: ...
    async def aplicar_edicion(self, venta_id: int, edicion: EdicionVenta) -> VentaConLineas | None: ...


@dataclass(frozen=True, slots=True)
class ResultadoVenta:
    venta: VentaLeer
    replay: bool  # True si se devolvió una venta ya existente (idempotencia)


def calcular_totales(lineas: list[LineaResuelta]) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal, impuestos, total) con IVA incluido en cada total de línea."""
    subtotal = impuestos = total = Decimal("0")
    for ln in lineas:
        impuesto = _money(ln.total_linea - ln.total_linea / (1 + Decimal(ln.iva) / 100))
        base = ln.total_linea - impuesto
        subtotal += base
        impuestos += impuesto
        total += ln.total_linea
    return _money(subtotal), _money(impuestos), _money(total)


class VentaService:
    def __init__(self, repo: VentasRepo) -> None:
        self._repo = repo

    async def registrar_venta(
        self, datos: VentaCrear, vendedor_id: int, *, control_stock_estricto: bool = False
    ) -> ResultadoVenta:
        """Registra la venta. `control_stock_estricto` (opt-in por empresa) bloquea con
        StockInsuficiente cuando el stock no alcanza; el default PERMISIVO (False) deja pasar la venta y
        el stock baja (puede quedar negativo: negocios informales que no llevan inventario estricto)."""
        if datos.idempotency_key:
            existente = await self._repo.buscar_por_idempotency(datos.idempotency_key)
            if existente is not None:
                return ResultadoVenta(venta=existente, replay=True)

        lineas = [await self._resolver_linea(ln, control_stock_estricto) for ln in datos.lineas]
        subtotal, impuestos, total = calcular_totales(lineas)
        consecutivo = await self._repo.siguiente_consecutivo()
        header = VentaHeader(
            consecutivo=consecutivo,
            cliente_id=datos.cliente_id,
            vendedor_id=vendedor_id,
            subtotal=subtotal,
            impuestos=impuestos,
            total=total,
            metodo_pago=datos.metodo_pago,
            origen=datos.origen,
            idempotency_key=datos.idempotency_key,
            lineas=lineas,
        )
        venta = await self._repo.crear_venta(header)
        return ResultadoVenta(venta=venta, replay=False)

    # --- Lecturas para el agente IA (solo lectura; no mutan) -------------------
    async def listar_dia(self, *, vendedor_id: int | None) -> list[VentaLeer]:
        """Ventas de HOY (Colombia). `vendedor_id` acota a un vendedor; None = todas. Solo lectura.

        El rango por defecto (hoy) lo resuelve el repo; el scope RBAC (vendedor → su id, admin → None)
        lo decide el handler de la herramienta, no el servicio.
        """
        return await self._repo.listar(desde=None, hasta=None, vendedor_id=vendedor_id)

    async def buscar_producto_por_nombre(self, texto: str) -> list[ProductoBusqueda]:
        """Coincidencias de catálogo por nombre, enriquecidas (precio, stock, unidad, fracciones).

        Reusa la resolución nombre→producto del catálogo (la misma del resto del sistema) vía el repo;
        cada candidato se surte con `obtener_producto_busqueda` (precio base + stock + unidad de empaque
        + fracciones con etiqueta). El conteo está acotado por el límite del buscador, no es ilimitado.
        """
        candidatos = await self._repo.buscar_productos_por_nombre(texto)
        productos: list[ProductoBusqueda] = []
        for prod_id, _ in candidatos:
            prod = await self._repo.obtener_producto_busqueda(prod_id)
            if prod is not None:
                productos.append(prod)
        return productos

    async def _guard_modificacion(
        self, venta_id: int, *, user_id: int, es_admin: bool, accion: str
    ) -> VentaLeer:
        """Guards compartidos de borrar/editar una venta; devuelve la cabecera si todos pasan.

        En orden: existe (VentaNoEncontrada/404) → es de HOY Colombia (VentaNoEsDeHoy/409) → permiso,
        admin o vendedor dueño (OperacionNoAutorizada/403) → sin factura electrónica viva
        (VentaConFacturaViva/409). `accion` ("borrar"/"editar") solo ajusta el mensaje del error.
        """
        venta = await self._repo.obtener_cabecera(venta_id)
        if venta is None:
            raise VentaNoEncontrada(venta_id)
        if not es_de_hoy_co(venta.fecha):
            raise VentaNoEsDeHoy(venta_id, accion=accion)
        if not (es_admin or venta.vendedor_id == user_id):
            raise OperacionNoAutorizada(venta_id, accion=accion)
        if await self._repo.tiene_factura_viva(venta_id):
            raise VentaConFacturaViva(venta_id, accion=accion)
        return venta

    async def borrar_venta(self, venta_id: int, *, user_id: int, es_admin: bool) -> int:
        """Borra una venta de HOY (Colombia) restaurando stock. Devuelve el `venta_id` borrado.

        Aplica los guards comunes (404/409/403/409) y, si pasa, delega el borrado físico transaccional
        (reversión de stock + movimientos) al repositorio.
        """
        await self._guard_modificacion(venta_id, user_id=user_id, es_admin=es_admin, accion="borrar")
        await self._repo.borrar_venta(venta_id)
        return venta_id

    async def editar_venta(
        self, venta_id: int, datos: VentaCrear, *, user_id: int, es_admin: bool,
        control_stock_estricto: bool = False,
    ) -> VentaConLineas:
        """Edita una venta de HOY EN EL LUGAR: mantiene id, consecutivo y fecha original.

        Mismos guards que el borrado (404/409/403/409, mensajes con verbo "editar"). Luego, en la misma
        transacción: revierte el stock de las líneas viejas (reusa la reversión del borrado), resuelve
        las líneas nuevas contra el stock YA restaurado (reusa `_resolver_linea`, respetando
        `control_stock_estricto`), recalcula totales y aplica el detalle nuevo + sus SALIDA. Devuelve la
        venta con sus líneas (`venta_editada` emitido por el repositorio).
        """
        await self._guard_modificacion(venta_id, user_id=user_id, es_admin=es_admin, accion="editar")
        # Revertir ANTES de resolver: así `lock_inventario` ve el stock ya restaurado (el modo estricto
        # cuenta como disponible lo que liberan las líneas viejas).
        await self._repo.revertir_lineas(venta_id)
        lineas = [await self._resolver_linea(ln, control_stock_estricto) for ln in datos.lineas]
        subtotal, impuestos, total = calcular_totales(lineas)
        edicion = EdicionVenta(
            cliente_id=datos.cliente_id, metodo_pago=datos.metodo_pago,
            subtotal=subtotal, impuestos=impuestos, total=total, lineas=lineas,
        )
        venta = await self._repo.aplicar_edicion(venta_id, edicion)
        if venta is None:  # carrera improbable: la venta se borró entre el guard y el apply
            raise VentaNoEncontrada(venta_id)
        return venta

    async def _resolver_linea(self, ln, control_stock_estricto: bool) -> LineaResuelta:
        if ln.producto_id is None:
            return self._linea_varia(ln)
        return await self._linea_catalogo(ln, control_stock_estricto)

    def _linea_varia(self, ln) -> LineaResuelta:
        if ln.precio_unitario is None or not ln.descripcion:
            raise LineaInvalida("Venta varia sin precio_unitario o descripcion")
        total = _money(ln.precio_unitario * ln.cantidad)
        return LineaResuelta(
            producto_id=None, descripcion=ln.descripcion, cantidad=ln.cantidad,
            precio_unitario=ln.precio_unitario, iva=ln.iva or 0,
            total_linea=total, descontar_stock=False,
        )

    async def _linea_catalogo(self, ln, control_stock_estricto: bool) -> LineaResuelta:
        prod = await self._repo.obtener_producto(ln.producto_id)
        if prod is None or not prod.activo:
            raise ProductoNoEncontrado(ln.producto_id)
        # Siempre se bloquea el inventario (FOR UPDATE) para descontar en la misma tx, aun en modo
        # permisivo. Solo el modo ESTRICTO (opt-in por empresa) rechaza cuando el stock no alcanza;
        # en permisivo se deja pasar y el stock baja (crear_venta descuenta sin clamp → negativo OK).
        disponible = await self._repo.lock_inventario(ln.producto_id)
        disponible = disponible if disponible is not None else Decimal("0")
        if control_stock_estricto and disponible < ln.cantidad:
            raise StockInsuficiente(ln.producto_id, disponible, ln.cantidad)
        # Precio declarado (override explícito) gana; si no, el motor de precios es la fuente
        # de verdad: escalonado por umbral → fracción → simple (ferrebot-logica-portar.md §3).
        if ln.precio_unitario is not None:
            precio = ln.precio_unitario
            total = _money(precio * ln.cantidad)
        else:
            total, precio = obtener_precio_para_cantidad(prod.esquema(), ln.cantidad)
        return LineaResuelta(
            producto_id=prod.id, descripcion=ln.descripcion or prod.nombre, cantidad=ln.cantidad,
            precio_unitario=precio, iva=prod.iva, total_linea=total, descontar_stock=True,
            costo_unitario=prod.precio_compra,
        )
