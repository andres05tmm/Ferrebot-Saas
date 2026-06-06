"""Servicio de ventas: lógica de dominio pura y testeable (sin SQL directo).

Depende del protocolo `VentasRepo`; los tests unitarios inyectan un repo falso. Calcula
totales con IVA INCLUIDO en el precio (estándar retail Colombia): el precio del catálogo es
la fuente de verdad (ferrebot-logica-portar.md §1). El consecutivo sale de una SEQUENCE.
"""
from dataclasses import dataclass, field
from datetime import datetime
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
    BorradoNoAutorizado,
    LineaInvalida,
    ProductoNoEncontrado,
    StockInsuficiente,
    VentaConFacturaViva,
    VentaNoEncontrada,
    VentaNoEsDeHoy,
)
from modules.ventas.schemas import VentaCrear, VentaLeer


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

    def esquema(self) -> EsquemaPrecio:
        """Arma el esquema que consume el motor de precios (modules.inventario)."""
        return EsquemaPrecio(
            precio_venta=self.precio_venta,
            precio_umbral=self.precio_umbral,
            precio_bajo_umbral=self.precio_bajo_umbral,
            precio_sobre_umbral=self.precio_sobre_umbral,
            fracciones=self.fracciones,
        )


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


class VentasRepo(Protocol):
    """Puerto de datos de ventas (lo implementa SqlVentasRepository; los tests lo falsean)."""

    async def buscar_por_idempotency(self, key: str) -> VentaLeer | None: ...
    async def obtener_producto(self, producto_id: int) -> ProductoPrecio | None: ...
    async def lock_inventario(self, producto_id: int) -> Decimal | None: ...
    async def siguiente_consecutivo(self) -> int: ...
    async def crear_venta(self, header: VentaHeader) -> VentaLeer: ...
    async def obtener_cabecera(self, venta_id: int) -> VentaLeer | None: ...
    async def tiene_factura_viva(self, venta_id: int) -> bool: ...
    async def borrar_venta(self, venta_id: int) -> None: ...


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

    async def borrar_venta(self, venta_id: int, *, user_id: int, es_admin: bool) -> int:
        """Borra una venta de HOY (Colombia) restaurando stock. Devuelve el `venta_id` borrado.

        Guards (en orden): existe (VentaNoEncontrada/404) → es de hoy (VentaNoEsDeHoy/409) →
        permiso, admin o vendedor dueño (BorradoNoAutorizado/403) → sin factura electrónica viva
        (VentaConFacturaViva/409). Si pasa, delega el borrado físico transaccional al repositorio.
        """
        venta = await self._repo.obtener_cabecera(venta_id)
        if venta is None:
            raise VentaNoEncontrada(venta_id)
        if not es_de_hoy_co(venta.fecha):
            raise VentaNoEsDeHoy(venta_id)
        if not (es_admin or venta.vendedor_id == user_id):
            raise BorradoNoAutorizado(venta_id)
        if await self._repo.tiene_factura_viva(venta_id):
            raise VentaConFacturaViva(venta_id)
        await self._repo.borrar_venta(venta_id)
        return venta_id

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
