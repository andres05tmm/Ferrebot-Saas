"""Repositorio del pack ventas/cotizaciones: único lugar con SQL (regla no negociable #2).

El catálogo y los precios se LEEN del POS: la resolución de nombres reusa `BuscadorProductos`
(4 capas) y el cálculo reusa el motor real de precios de inventario (escalonado por cantidad).
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.cotizaciones.models import Cotizacion, CotizacionItem, VentasWaConfig
from modules.cotizaciones.schemas import VentasWaConfigActualizar
from modules.inventario.busqueda import BuscadorProductos, ResultadoBusqueda
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService, PrecioCalculado


class SqlCotizacionesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._inventario = InventarioService(SqlInventarioRepository(session))

    # --- config (una fila, get-or-create con defaults) ------------------------
    async def obtener_config(self) -> VentasWaConfig:
        config = (await self._s.execute(select(VentasWaConfig).limit(1))).scalar_one_or_none()
        if config is None:
            config = VentasWaConfig()
            self._s.add(config)
            await self._s.flush()
        return config

    async def guardar_config(self, datos: VentasWaConfigActualizar) -> VentasWaConfig:
        config = await self.obtener_config()
        for campo, valor in datos.model_dump().items():
            setattr(config, campo, valor)
        await self._s.flush()
        return config

    # --- catálogo (solo lectura, motores reales) ---------------------------------
    async def buscar_producto(self, texto: str, *, limite: int = 5) -> ResultadoBusqueda:
        return await BuscadorProductos(SqlInventarioRepository(self._s)).buscar(texto, limite=limite)

    async def calcular_precio(self, producto_id: int, cantidad: Decimal) -> PrecioCalculado:
        """Precio REAL para la cantidad (escalonado/fracciones incluidos): el agente jamás calcula."""
        return await self._inventario.calcular_precio(producto_id, cantidad)

    async def stock_de(self, producto_id: int) -> Decimal:
        fila = (
            await self._s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :pid"),
                {"pid": producto_id},
            )
        ).first()
        return Decimal(fila[0]) if fila else Decimal("0")

    # --- carrito / cotizaciones -----------------------------------------------------
    async def abierta_de(self, telefono: str) -> Cotizacion | None:
        return (
            await self._s.execute(
                select(Cotizacion)
                .where(Cotizacion.cliente_telefono == telefono, Cotizacion.estado == "abierta")
                .order_by(Cotizacion.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def crear_abierta(self, telefono: str, *, idempotency_key: str | None) -> Cotizacion:
        cotizacion = Cotizacion(cliente_telefono=telefono, idempotency_key=idempotency_key)
        self._s.add(cotizacion)
        await self._s.flush()
        return cotizacion

    async def cargar_items(self, cotizacion: Cotizacion) -> Cotizacion:
        """Carga explícita de la relación (tocarla sin cargar = lazy-load síncrono → greenlet)."""
        await self._s.refresh(cotizacion, attribute_names=["items"])
        return cotizacion

    async def upsert_item(
        self, cotizacion: Cotizacion, *, producto_id: int, nombre: str,
        cantidad: Decimal, precio_unitario: Decimal, subtotal: Decimal,
    ) -> Cotizacion:
        """Agrega la línea o la actualiza si el producto ya está (recotizado por nueva cantidad)."""
        await self.cargar_items(cotizacion)
        existente = next((i for i in cotizacion.items if i.producto_id == producto_id), None)
        if existente is not None:
            existente.cantidad = cantidad
            existente.precio_unitario = precio_unitario
            existente.subtotal = subtotal
        else:
            cotizacion.items.append(CotizacionItem(
                producto_id=producto_id, nombre=nombre, cantidad=cantidad,
                precio_unitario=precio_unitario, subtotal=subtotal,
            ))
        await self._recalcular(cotizacion)
        return cotizacion

    async def quitar_item(self, cotizacion: Cotizacion, producto_id: int) -> bool:
        await self.cargar_items(cotizacion)
        existente = next((i for i in cotizacion.items if i.producto_id == producto_id), None)
        if existente is None:
            return False
        cotizacion.items.remove(existente)
        await self._recalcular(cotizacion)
        return True

    async def _recalcular(self, cotizacion: Cotizacion) -> None:
        cotizacion.total = sum((i.subtotal for i in cotizacion.items), Decimal("0"))
        await self._s.flush()
        await self._s.refresh(cotizacion, attribute_names=["actualizado_en"])   # onupdate la expiró

    async def emitir(self, cotizacion: Cotizacion, *, vigencia_hasta: date) -> Cotizacion:
        cotizacion.estado = "emitida"
        cotizacion.vigencia_hasta = vigencia_hasta
        await self._s.flush()
        await self._s.refresh(cotizacion, attribute_names=["actualizado_en"])
        await publish(self._s, "cotizacion_emitida", {
            "cotizacion_id": cotizacion.id, "total": str(cotizacion.total),
        })
        return cotizacion

    async def marcar(self, cotizacion: Cotizacion, estado: str) -> Cotizacion:
        cotizacion.estado = estado
        await self._s.flush()
        await self._s.refresh(cotizacion, attribute_names=["actualizado_en"])
        await publish(self._s, "cotizacion_estado", {
            "cotizacion_id": cotizacion.id, "estado": estado,
        })
        return cotizacion

    async def cotizacion_por_id(self, cotizacion_id: int) -> Cotizacion | None:
        return await self._s.get(Cotizacion, cotizacion_id)

    async def vencer_expiradas(self, *, hoy: date) -> int:
        """Barrido perezoso al listar: emitidas con vigencia vencida → `vencida`."""
        resultado = await self._s.execute(
            update(Cotizacion)
            .where(Cotizacion.estado == "emitida", Cotizacion.vigencia_hasta < hoy)
            .values(estado="vencida")
        )
        await self._s.flush()
        return resultado.rowcount or 0

    async def listar(self, *, estados: list[str] | None = None, limite: int = 200) -> list[Cotizacion]:
        consulta = select(Cotizacion).order_by(Cotizacion.creado_en.desc()).limit(limite)
        if estados:
            consulta = consulta.where(Cotizacion.estado.in_(estados))
        return list((await self._s.execute(consulta)).scalars())
