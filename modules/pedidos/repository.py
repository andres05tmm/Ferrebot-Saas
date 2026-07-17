"""Repositorio del pack pedidos: único lugar con SQL (regla no negociable #2).

El catálogo (`productos` + `inventario`) solo se LEE: el pedido jamás descuenta stock (regla #7 —
el stock cambia cuando el negocio convierta el pedido en venta, no antes). La resolución de nombres
reusa el `BuscadorProductos` de inventario (exacta → alias → trigram → fuzzy).
"""
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.inventario.busqueda import BuscadorProductos, ResultadoBusqueda
from modules.inventario.repository import SqlInventarioRepository
from modules.pagos.models import Cobro
from modules.pedidos.models import Pedido, PedidoConfig, PedidoItem, ZonaDomicilio
from modules.pedidos.schemas import PedidoConfigActualizar, ZonaCrear


class SqlPedidosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- config (una fila, get-or-create con defaults) ------------------------
    async def obtener_config(self) -> PedidoConfig:
        config = (await self._s.execute(select(PedidoConfig).limit(1))).scalar_one_or_none()
        if config is None:
            config = PedidoConfig()
            self._s.add(config)
            await self._s.flush()
        return config

    async def guardar_config(self, datos: PedidoConfigActualizar) -> PedidoConfig:
        config = await self.obtener_config()
        for campo, valor in datos.model_dump().items():
            setattr(config, campo, valor)
        await self._s.flush()
        return config

    # --- catálogo (solo lectura) -----------------------------------------------
    async def buscar_producto(self, texto: str, *, limite: int = 5) -> ResultadoBusqueda:
        """Candidatos del catálogo con la resolución de 4 capas del sistema."""
        return await BuscadorProductos(SqlInventarioRepository(self._s)).buscar(texto, limite=limite)

    async def producto_para_menu(self, producto_id: int) -> dict | None:
        """Nombre, precio y stock disponible de un producto activo (None si no existe/inactivo)."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT p.id, p.nombre, p.precio_venta, p.unidad_medida, "
                    "       COALESCE(i.stock_actual, 0) AS stock "
                    "FROM productos p LEFT JOIN inventario i ON i.producto_id = p.id "
                    "WHERE p.id = :pid AND p.activo"
                ),
                {"pid": producto_id},
            )
        ).first()
        return dict(fila._mapping) if fila else None

    async def menu(self, *, limite: int = 20) -> list[dict]:
        """Productos activos con stock para mostrar el menú (sin búsqueda)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT p.id, p.nombre, p.precio_venta, p.unidad_medida, "
                    "       COALESCE(i.stock_actual, 0) AS stock "
                    "FROM productos p LEFT JOIN inventario i ON i.producto_id = p.id "
                    "WHERE p.activo AND COALESCE(i.stock_actual, 0) > 0 "
                    "ORDER BY p.nombre LIMIT :lim"
                ),
                {"lim": limite},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    # --- zonas de domicilio ------------------------------------------------------
    async def listar_zonas(self, *, solo_activas: bool = True) -> list[ZonaDomicilio]:
        consulta = select(ZonaDomicilio).order_by(ZonaDomicilio.nombre)
        if solo_activas:
            consulta = consulta.where(ZonaDomicilio.activo.is_(True))
        return list((await self._s.execute(consulta)).scalars())

    async def crear_zona(self, datos: ZonaCrear) -> ZonaDomicilio:
        zona = ZonaDomicilio(**datos.model_dump())
        self._s.add(zona)
        await self._s.flush()
        return zona

    async def zona_por_id(self, zona_id: int) -> ZonaDomicilio | None:
        return await self._s.get(ZonaDomicilio, zona_id)

    async def zona_por_nombre(self, nombre: str) -> ZonaDomicilio | None:
        """Match laxo del barrio que dice el cliente (contiene, case-insensitive)."""
        return (
            await self._s.execute(
                select(ZonaDomicilio)
                .where(
                    ZonaDomicilio.activo.is_(True),
                    ZonaDomicilio.nombre.ilike(f"%{nombre.strip()}%"),
                )
                .order_by(ZonaDomicilio.id)
                .limit(1)
            )
        ).scalar_one_or_none()

    # --- pedidos --------------------------------------------------------------------
    async def pedido_por_id(self, pedido_id: int) -> Pedido | None:
        return await self._s.get(Pedido, pedido_id)

    async def pedido_por_key(self, idempotency_key: str) -> Pedido | None:
        return (
            await self._s.execute(
                select(Pedido).where(Pedido.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def borrador_de(self, telefono: str) -> Pedido | None:
        """El pedido en armado (`recibido`) del que escribe: uno por teléfono."""
        return (
            await self._s.execute(
                select(Pedido)
                .where(Pedido.cliente_telefono == telefono, Pedido.estado == "recibido")
                .order_by(Pedido.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def ultimo_de(self, telefono: str) -> Pedido | None:
        """El último pedido del que escribe (cualquier estado)."""
        return (
            await self._s.execute(
                select(Pedido)
                .where(Pedido.cliente_telefono == telefono)
                .order_by(Pedido.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def crear_pedido(
        self, *, telefono: str, notas: str | None, idempotency_key: str | None, origen: str
    ) -> Pedido:
        pedido = Pedido(
            cliente_telefono=telefono, notas=notas,
            idempotency_key=idempotency_key, origen=origen,
        )
        self._s.add(pedido)
        await self._s.flush()
        return pedido

    async def reemplazar_items(self, pedido: Pedido, items: list[dict]) -> Pedido:
        """Reescribe los ítems del borrador (volver a armar = reemplazar) y recalcula el subtotal."""
        # Carga explícita de la relación: tocarla sin cargar dispara lazy-load síncrono (greenlet).
        await self._s.refresh(pedido, attribute_names=["items"])
        pedido.items.clear()
        subtotal = Decimal("0")
        for item in items:
            pedido.items.append(PedidoItem(**item))
            subtotal += item["subtotal"]
        pedido.subtotal = subtotal
        pedido.total = subtotal + pedido.costo_domicilio
        await self._s.flush()
        return pedido

    async def confirmar(
        self, pedido: Pedido, *, direccion: str, zona_id: int | None,
        costo_domicilio: Decimal, metodo_pago: str, nombre: str | None,
    ) -> Pedido:
        pedido.direccion = direccion
        pedido.zona_id = zona_id
        pedido.costo_domicilio = costo_domicilio
        pedido.metodo_pago = metodo_pago
        if nombre:
            pedido.cliente_nombre = nombre
        pedido.total = pedido.subtotal + costo_domicilio
        pedido.estado = "confirmado"
        await self._s.flush()
        # El onupdate server-side expira `actualizado_en`: refresco explícito (serializar sin esto
        # dispara un lazy-refresh síncrono → MissingGreenlet).
        await self._s.refresh(pedido, attribute_names=["actualizado_en"])
        await publish(self._s, "pedido_confirmado", {
            "pedido_id": pedido.id, "total": str(pedido.total),
        })
        return pedido

    async def cambiar_estado(self, pedido: Pedido, nuevo: str) -> Pedido:
        pedido.estado = nuevo
        await self._s.flush()
        await self._s.refresh(pedido, attribute_names=["actualizado_en"])   # onupdate lo expiró
        await publish(self._s, "pedido_estado", {"pedido_id": pedido.id, "estado": nuevo})
        return pedido

    async def listar(self, *, estados: list[str] | None = None, limite: int = 200) -> list[Pedido]:
        """Pedidos para el kanban (más recientes primero), opcionalmente filtrados por estado.

        Anota en cada pedido el atributo transitorio `pagado` (bool) según exista un cobro
        `pagado` por (origen="pedido", origen_id=pedido.id) — la insignia "Pagado ✓" del kanban.
        """
        consulta = select(Pedido).order_by(Pedido.creado_en.desc()).limit(limite)
        if estados:
            consulta = consulta.where(Pedido.estado.in_(estados))
        pedidos = list((await self._s.execute(consulta)).scalars())
        await self._anotar_pagados(pedidos)
        return pedidos

    async def _anotar_pagados(self, pedidos: list[Pedido]) -> None:
        """Marca `pedido.pagado` en lote: UNA consulta a `cobros` por el lote entero (sin N+1).

        La tabla `cobros` vive en la misma base del tenant; se lee por la capa de repositorio.
        """
        ids = [p.id for p in pedidos]
        pagados: set[int] = set()
        if ids:
            filas = await self._s.execute(
                select(Cobro.origen_id).where(
                    Cobro.origen == "pedido",
                    Cobro.estado == "pagado",
                    Cobro.origen_id.in_(ids),
                )
            )
            pagados = set(filas.scalars().all())
        for pedido in pedidos:
            pedido.pagado = pedido.id in pagados
