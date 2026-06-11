"""Motor del pack ventas/cotizaciones (ADR 0017): determinista, igual para todos los tenants.

El agente NUNCA inventa precio ni stock: la resolución usa el buscador real (4 capas) y el precio
sale del motor real de inventario (escalonado por cantidad). Identidad = el teléfono que escribe.
La cotización emitida es un snapshot con vigencia; el catálogo puede cambiar después, ella no.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from modules.cotizaciones.errors import (
    CarritoVacio,
    CotizacionInexistente,
    EstadoInvalido,
    ProductoNoResuelto,
)
from modules.cotizaciones.models import Cotizacion
from modules.cotizaciones.repository import SqlCotizacionesRepository
from modules.cotizaciones.schemas import VentasWaConfigActualizar
from modules.inventario.errors import ProductoInexistente


@dataclass(frozen=True, slots=True)
class ItemCotizar:
    """Un ítem como lo pide el cliente: nombre libre + cantidad. El motor lo resuelve."""

    producto: str
    cantidad: Decimal


@dataclass(frozen=True, slots=True)
class PrecioCotizado:
    """Respuesta de una consulta de precio: producto real + precio del motor + stock (si se muestra)."""

    producto_id: int
    nombre: str
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal
    regla: str                  # base | escalonado | fraccion (lo que aplicó el motor)
    stock: Decimal | None       # None = el negocio no muestra stock


class CotizacionesService:
    def __init__(self, repo: SqlCotizacionesRepository) -> None:
        self._repo = repo

    async def _resolver(self, texto: str) -> int:
        """Texto del cliente → producto_id por el buscador real. Lanza con sugerencias si no resuelve."""
        resultado = await self._repo.buscar_producto(texto)
        resuelto = next((c for c in resultado.coincidencias if not c.sugerencia), None)
        if resuelto is None:
            sugerencias = [c.nombre for c in resultado.coincidencias][:3]
            raise ProductoNoResuelto(texto, sugerencias)
        return resuelto.producto_id

    # --- consulta de precio (sin carrito) ----------------------------------------
    async def cotizar(self, texto: str, cantidad: Decimal = Decimal("1")) -> PrecioCotizado:
        producto_id = await self._resolver(texto)
        try:
            precio = await self._repo.calcular_precio(producto_id, cantidad)
        except ProductoInexistente as exc:
            raise ProductoNoResuelto(texto, []) from exc
        config = await self._repo.obtener_config()
        resultado = await self._repo.buscar_producto(texto)
        nombre = next(c.nombre for c in resultado.coincidencias if c.producto_id == producto_id)
        return PrecioCotizado(
            producto_id=producto_id, nombre=nombre, cantidad=cantidad,
            precio_unitario=precio.precio_unitario, total=precio.total, regla=precio.regla,
            stock=(await self._repo.stock_de(producto_id)) if config.mostrar_stock else None,
        )

    # --- carrito (uno `abierta` por teléfono) ---------------------------------------
    async def agregar(
        self, telefono: str, items: list[ItemCotizar], *, idempotency_key: str | None = None
    ) -> Cotizacion:
        """Agrega (o actualiza por producto) líneas al carrito del que escribe, recotizando el precio."""
        cotizacion = await self._repo.abierta_de(telefono)
        if cotizacion is None:
            cotizacion = await self._repo.crear_abierta(telefono, idempotency_key=idempotency_key)
        for item in items:
            producto_id = await self._resolver(item.producto)
            precio = await self._repo.calcular_precio(producto_id, item.cantidad)
            resultado = await self._repo.buscar_producto(item.producto)
            nombre = next(c.nombre for c in resultado.coincidencias if c.producto_id == producto_id)
            cotizacion = await self._repo.upsert_item(
                cotizacion, producto_id=producto_id, nombre=nombre, cantidad=item.cantidad,
                precio_unitario=precio.precio_unitario, subtotal=precio.total,
            )
        return cotizacion

    async def quitar(self, telefono: str, texto: str) -> Cotizacion:
        cotizacion = await self._repo.abierta_de(telefono)
        if cotizacion is None:
            raise CarritoVacio()
        producto_id = await self._resolver(texto)
        if not await self._repo.quitar_item(cotizacion, producto_id):
            raise ProductoNoResuelto(texto, [])
        return cotizacion

    async def ver(self, telefono: str) -> Cotizacion | None:
        cotizacion = await self._repo.abierta_de(telefono)
        if cotizacion is not None:
            await self._repo.cargar_items(cotizacion)
        return cotizacion

    async def emitir(self, telefono: str, *, hoy: date) -> Cotizacion:
        """Cierra el carrito: estado `emitida` + vigencia. El resumen viaja por el chat (PDF = v2)."""
        cotizacion = await self._repo.abierta_de(telefono)
        if cotizacion is None:
            raise CarritoVacio()
        await self._repo.cargar_items(cotizacion)
        if not cotizacion.items:
            raise CarritoVacio()
        config = await self._repo.obtener_config()
        return await self._repo.emitir(
            cotizacion, vigencia_hasta=hoy + timedelta(days=config.vigencia_dias)
        )

    # --- dashboard --------------------------------------------------------------------
    async def listar(self, *, estados: list[str] | None = None, hoy: date) -> list[Cotizacion]:
        await self._repo.vencer_expiradas(hoy=hoy)   # barrido perezoso (sin cron)
        return await self._repo.listar(estados=estados)

    async def marcar(self, cotizacion_id: int, estado: str) -> Cotizacion:
        cotizacion = await self._repo.cotizacion_por_id(cotizacion_id)
        if cotizacion is None:
            raise CotizacionInexistente(str(cotizacion_id))
        if cotizacion.estado not in ("emitida", "abierta"):
            raise EstadoInvalido(cotizacion.estado, estado)
        return await self._repo.marcar(cotizacion, estado)

    async def obtener_config(self):
        return await self._repo.obtener_config()

    async def guardar_config(self, datos: VentasWaConfigActualizar):
        return await self._repo.guardar_config(datos)
