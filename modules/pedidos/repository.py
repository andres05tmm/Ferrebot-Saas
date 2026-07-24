"""Repositorio del pack pedidos: único lugar con SQL (regla no negociable #2).

El catálogo (`productos` + `inventario`) solo se LEE: el pedido jamás descuenta stock (regla #7 —
el stock cambia cuando el negocio convierta el pedido en venta, no antes). La resolución de nombres
reusa el `BuscadorProductos` de inventario (exacta → alias → trigram → fuzzy).
"""
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.inventario.busqueda import BuscadorProductos, ResultadoBusqueda
from modules.inventario.repository import SqlInventarioRepository
from modules.pagos.models import Cobro
from modules.pedidos.models import (
    Comanda,
    ComandaItem,
    ComandaZona,
    Mesa,
    ModificadorGrupo,
    Pedido,
    PedidoConfig,
    PedidoItem,
    ZonaDomicilio,
)
from modules.pedidos.schemas import PedidoConfigActualizar, ZonaCrear


class SqlPedidosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @property
    def sesion(self) -> AsyncSession:
        """La sesión del tenant (la usa el servicio KDS para emitir su evento SSE)."""
        return self._s

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
        """Nombre, precio y stock disponible de un producto activo (None si no existe/inactivo).

        `stock` NULL = el producto NO lleva fila de inventario (restaurante que no controla stock
        de platos): el motor no bloquea por stock — coherente con el permisivo de la venta.
        """
        fila = (
            await self._s.execute(
                text(
                    "SELECT p.id, p.nombre, p.precio_venta, p.unidad_medida, "
                    "       i.stock_actual AS stock "
                    "FROM productos p LEFT JOIN inventario i ON i.producto_id = p.id "
                    "WHERE p.id = :pid AND p.activo"
                ),
                {"pid": producto_id},
            )
        ).first()
        return dict(fila._mapping) if fila else None

    async def menu(self, *, limite: int = 20) -> list[dict]:
        """Productos activos disponibles: con stock, SIN control de stock (sin fila de inventario —
        restaurante que no lleva kárdex de platos) o con RECETA (su stock es de los insumos, F6)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT p.id, p.nombre, p.precio_venta, p.unidad_medida, "
                    "       i.stock_actual AS stock "
                    "FROM productos p LEFT JOIN inventario i ON i.producto_id = p.id "
                    "WHERE p.activo AND (i.stock_actual IS NULL OR i.stock_actual > 0 "
                    "      OR EXISTS (SELECT 1 FROM recetas r WHERE r.producto_id = p.id)) "
                    "ORDER BY p.nombre LIMIT :lim"
                ),
                {"lim": limite},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    # --- reportes restauranteros (F7 / ADR 0032) ---------------------------------------
    async def resumen_dia(self) -> dict:
        """Resumen del día (hora Colombia): pedidos por canal, top platos y tiempo medio de ciclo.

        Canal = `origen` del pedido (whatsapp/mesa/…). Solo cuenta pedidos NO cancelados de HOY;
        vendido = los convertidos en venta (venta_id). Tiempo de ciclo = creado → convertido.
        """
        from core.config.timezone import rango_dia_co

        inicio, fin = rango_dia_co()
        canales = (
            await self._s.execute(
                text(
                    "SELECT origen, count(*) AS pedidos, "
                    "       count(venta_id) AS vendidos, "
                    "       COALESCE(sum(total) FILTER (WHERE venta_id IS NOT NULL), 0) AS vendido "
                    "FROM pedidos WHERE creado_en BETWEEN :i AND :f AND estado <> 'cancelado' "
                    "GROUP BY origen ORDER BY origen"
                ),
                {"i": inicio, "f": fin},
            )
        ).all()
        top = (
            await self._s.execute(
                text(
                    "SELECT pi.nombre, sum(pi.cantidad) AS unidades, sum(pi.subtotal) AS total "
                    "FROM pedido_items pi JOIN pedidos p ON p.id = pi.pedido_id "
                    "WHERE p.creado_en BETWEEN :i AND :f AND p.estado <> 'cancelado' "
                    "GROUP BY pi.nombre ORDER BY unidades DESC LIMIT 5"
                ),
                {"i": inicio, "f": fin},
            )
        ).all()
        ciclo = (
            await self._s.execute(
                text(
                    "SELECT avg(EXTRACT(EPOCH FROM (convertido_en - creado_en)) / 60.0) AS minutos "
                    "FROM pedidos WHERE creado_en BETWEEN :i AND :f AND convertido_en IS NOT NULL"
                ),
                {"i": inicio, "f": fin},
            )
        ).scalar_one_or_none()
        return {
            "canales": [dict(f._mapping) for f in canales],
            "top_platos": [dict(f._mapping) for f in top],
            "ciclo_medio_min": float(ciclo) if ciclo is not None else None,
        }

    async def ingenieria_menu(self, *, dias: int = 30) -> list[dict]:
        """Ingeniería de menú (F7): por plato CON receta — margen (precio − costo insumos, F6) ×
        rotación (unidades pedidas en la ventana). El clásico margen×rotación del menú."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT p.id, p.nombre, p.precio_venta, "
                    "       COALESCE(SUM(COALESCE(ins.costo_promedio, ins.precio_compra, 0) * r.cantidad), 0) AS costo_plato, "
                    "       COALESCE(( "
                    "           SELECT sum(pi.cantidad) FROM pedido_items pi "
                    "           JOIN pedidos pe ON pe.id = pi.pedido_id "
                    "           WHERE pi.producto_id = p.id AND pe.estado <> 'cancelado' "
                    "             AND pe.creado_en >= now() - make_interval(days => :dias) "
                    "       ), 0) AS rotacion "
                    "FROM productos p "
                    "JOIN recetas r ON r.producto_id = p.id "
                    "JOIN productos ins ON ins.id = r.insumo_id "
                    "WHERE p.activo GROUP BY p.id, p.nombre, p.precio_venta ORDER BY p.nombre"
                ),
                {"dias": dias},
            )
        ).all()
        resultado = []
        for f in filas:
            margen = Decimal(f.precio_venta) - Decimal(f.costo_plato)
            resultado.append({
                "producto_id": f.id, "nombre": f.nombre,
                "precio_venta": f.precio_venta, "costo_plato": f.costo_plato,
                "margen": margen, "rotacion": f.rotacion,
                "margen_total": margen * Decimal(f.rotacion),
            })
        return resultado

    # --- recetas / BOM (F6 / ADR 0032 D9) ---------------------------------------------
    async def receta_de(self, producto_id: int) -> list[dict]:
        """Insumos de la receta del plato: [{insumo_id, nombre, cantidad, costo_unitario}]."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT r.insumo_id, p.nombre, r.cantidad, "
                    "       COALESCE(p.costo_promedio, p.precio_compra) AS costo_unitario "
                    "FROM recetas r JOIN productos p ON p.id = r.insumo_id "
                    "WHERE r.producto_id = :pid ORDER BY p.nombre"
                ),
                {"pid": producto_id},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    async def reemplazar_receta(self, producto_id: int, insumos: list[dict]) -> None:
        """Reescribe la receta del plato (edición admin): borra y re-inserta."""
        await self._s.execute(
            text("DELETE FROM recetas WHERE producto_id = :pid"), {"pid": producto_id}
        )
        for insumo in insumos:
            await self._s.execute(
                text(
                    "INSERT INTO recetas (producto_id, insumo_id, cantidad) "
                    "VALUES (:pid, :i, :c)"
                ),
                {"pid": producto_id, "i": insumo["insumo_id"], "c": insumo["cantidad"]},
            )

    async def descontar_insumo(
        self, insumo_id: int, cantidad: Decimal, *, idempotency_key: str,
        referencia: str, usuario_id: int, costo_unitario: Decimal | None,
    ) -> Decimal | None:
        """SALIDA de un insumo por receta, IDEMPOTENTE por clave UNIQUE (regla #7 + #8).

        INSERT del movimiento con ON CONFLICT DO NOTHING: si la clave ya existe (reintento/replay)
        NO se descuenta stock de nuevo. Devuelve el stock resultante (None si fue replay) — el
        caller alerta si quedó negativo (insumo insuficiente NO bloquea; política ADR 0032 D9).
        """
        creado = (
            await self._s.execute(
                text(
                    "INSERT INTO movimientos_inventario "
                    "(producto_id, tipo, cantidad, costo_unitario, referencia, usuario_id, "
                    " idempotency_key, fecha_operacion) "
                    "VALUES (:p, 'SALIDA', :c, :cu, :ref, :u, :key, now()) "
                    "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING "
                    "RETURNING id"
                ),
                {"p": insumo_id, "c": cantidad, "cu": costo_unitario, "ref": referencia,
                 "u": usuario_id, "key": idempotency_key},
            )
        ).scalar_one_or_none()
        if creado is None:
            return None   # replay: el movimiento ya existía; el stock ya se descontó
        resultante = (
            await self._s.execute(
                text(
                    "UPDATE inventario SET stock_actual = stock_actual - :c "
                    "WHERE producto_id = :p RETURNING stock_actual"
                ),
                {"c": cantidad, "p": insumo_id},
            )
        ).scalar_one_or_none()
        if resultante is None:
            # Insumo sin fila de inventario (datos migrados): se crea en negativo honesto.
            resultante = (
                await self._s.execute(
                    text(
                        "INSERT INTO inventario (producto_id, stock_actual, stock_minimo) "
                        "VALUES (:p, 0 - :c, 0) RETURNING stock_actual"
                    ),
                    {"p": insumo_id, "c": cantidad},
                )
            ).scalar_one()
        return resultante

    async def modificadores_de(self, producto_id: int) -> list[ModificadorGrupo]:
        """Grupos ACTIVOS de modificadores del producto (con sus opciones, selectin), en orden."""
        return list(
            (
                await self._s.execute(
                    select(ModificadorGrupo)
                    .where(
                        ModificadorGrupo.producto_id == producto_id,
                        ModificadorGrupo.activo.is_(True),
                    )
                    .order_by(ModificadorGrupo.orden, ModificadorGrupo.id)
                )
            ).scalars()
        )

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

    async def pedido_para_conversion(self, pedido_id: int) -> Pedido | None:
        """El pedido bajo `FOR UPDATE` (serializa conversiones concurrentes, ADR 0022 D3)."""
        return (
            await self._s.execute(
                select(Pedido).where(Pedido.id == pedido_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def producto_activo(self, producto_id: int) -> bool:
        """¿El producto sigue activo en el catálogo? (decide catálogo vs línea varia al convertir)."""
        return bool(
            (
                await self._s.execute(
                    text("SELECT activo FROM productos WHERE id = :p"), {"p": producto_id}
                )
            ).scalar_one_or_none()
        )

    async def producto_info_conversion(self, producto_id: int) -> dict | None:
        """(activo, iva, tipo_impuesto, tiene_receta) del producto — decide la línea al convertir."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT activo, iva, tipo_impuesto, "
                    "       EXISTS (SELECT 1 FROM recetas r WHERE r.producto_id = p.id) AS tiene_receta "
                    "FROM productos p WHERE p.id = :p"
                ),
                {"p": producto_id},
            )
        ).first()
        return dict(fila._mapping) if fila else None

    async def vincular_venta(self, pedido: Pedido, venta_id: int) -> Pedido:
        """Vincula la venta (UNIQUE) y cierra el ciclo: el pedido convertido queda `entregado`.

        Misma transacción que la venta (el caller no comitea entre ambas). El paso directo a
        `entregado` es el evento contable de la conversión, no un arrastre del kanban.
        """
        pedido.venta_id = venta_id
        pedido.convertido_en = func.now()
        if pedido.estado not in ("entregado", "cancelado"):
            pedido.estado = "entregado"
        await self._s.flush()
        await self._s.refresh(pedido, attribute_names=["actualizado_en", "convertido_en"])
        await publish(self._s, "pedido_estado", {
            "pedido_id": pedido.id, "estado": pedido.estado, "venta_id": venta_id,
        })
        return pedido

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
        telefono_contacto: str | None = None,
    ) -> Pedido:
        pedido.direccion = direccion
        pedido.zona_id = zona_id
        pedido.costo_domicilio = costo_domicilio
        pedido.metodo_pago = metodo_pago
        if nombre:
            pedido.cliente_nombre = nombre
        if telefono_contacto:
            pedido.telefono_contacto = telefono_contacto
        pedido.total = pedido.subtotal + costo_domicilio
        pedido.estado = "confirmado"
        await self._s.flush()
        # El onupdate server-side expira `actualizado_en`: refresco explícito (serializar sin esto
        # dispara un lazy-refresh síncrono → MissingGreenlet).
        await self._s.refresh(pedido, attribute_names=["actualizado_en"])
        await publish(self._s, "pedido_confirmado", {
            "pedido_id": pedido.id, "total": str(pedido.total),
        })
        # KDS (F4): el pedido confirmado entra a la cocina — comandas por zona. Se generan siempre
        # (costo marginal nulo); la VISTA es la que está gateada por el flag `kds`.
        await self.crear_comandas(pedido, list(pedido.items))
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

    # --- comandas KDS (F4 / ADR 0032 D5) ----------------------------------------------
    async def crear_comandas(self, pedido: Pedido, items: list[PedidoItem]) -> list[Comanda]:
        """Genera las comandas de estos ítems agrupando por la zona del producto (NULL = cocina).

        Se llama al CONFIRMAR el pedido (todos sus ítems) y por cada RONDA de mesa (los nuevos).
        El KDS es una vista: el ítem se referencia, no se copia precio.
        """
        if not items:
            return []
        ids = [i.producto_id for i in items if i.producto_id is not None]
        zonas: dict[int, int | None] = {}
        if ids:
            filas = await self._s.execute(
                text("SELECT id, zona_comanda_id FROM productos WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            zonas = {f.id: f.zona_comanda_id for f in filas}
        por_zona: dict[int | None, list[PedidoItem]] = {}
        for item in items:
            zona = zonas.get(item.producto_id) if item.producto_id is not None else None
            por_zona.setdefault(zona, []).append(item)
        comandas: list[Comanda] = []
        for zona_id, grupo in por_zona.items():
            comanda = Comanda(pedido_id=pedido.id, zona_id=zona_id)
            comanda.items = [
                ComandaItem(pedido_item_id=i.id, cantidad=i.cantidad) for i in grupo
            ]
            self._s.add(comanda)
            comandas.append(comanda)
        await self._s.flush()
        await publish(self._s, "comanda_nueva", {
            "pedido_id": pedido.id, "comandas": [c.id for c in comandas],
        })
        # Impresión (ADR 0033 D2): un trabajo POR comanda, en la MISMA transacción e idempotente
        # por UNIQUE. Import local: evita el ciclo pedidos↔impresion en el arranque.
        from modules.impresion.generacion import generar_trabajos_comandas

        await generar_trabajos_comandas(
            self._s, pedido_id=pedido.id, comanda_ids=[c.id for c in comandas]
        )
        return comandas

    async def comanda_por_id(self, comanda_id: int) -> Comanda | None:
        return await self._s.get(Comanda, comanda_id)

    async def avanzar_comanda(self, comanda: Comanda, nuevo: str) -> Comanda:
        """Aplica la transición (ya validada por el servicio) con su timestamp de auditoría."""
        comanda.estado = nuevo
        if nuevo == "en_preparacion":
            comanda.iniciada_en = func.now()
        elif nuevo == "listo":
            comanda.lista_en = func.now()
        await self._s.flush()
        await self._s.refresh(comanda, attribute_names=["iniciada_en", "lista_en", "items"])
        await publish(self._s, "comanda_estado", {
            "comanda_id": comanda.id, "pedido_id": comanda.pedido_id, "estado": nuevo,
        })
        return comanda

    async def listar_comandas(self, *, estados: list[str] | None = None) -> list[Comanda]:
        consulta = select(Comanda).order_by(Comanda.creada_en)
        if estados:
            consulta = consulta.where(Comanda.estado.in_(estados))
        return list((await self._s.execute(consulta)).scalars())

    async def comandas_de_pedido(self, pedido_id: int) -> list[Comanda]:
        return list(
            (
                await self._s.execute(select(Comanda).where(Comanda.pedido_id == pedido_id))
            ).scalars()
        )

    async def pedido_items_por_ids(self, ids: list[int]) -> dict[int, PedidoItem]:
        """Los ítems de pedido referenciados por las comandas, en UNA consulta (sin N+1)."""
        if not ids:
            return {}
        filas = (
            await self._s.execute(select(PedidoItem).where(PedidoItem.id.in_(ids)))
        ).scalars()
        return {i.id: i for i in filas}

    async def listar_zonas_comanda(self) -> list[ComandaZona]:
        return list(
            (
                await self._s.execute(
                    select(ComandaZona).where(ComandaZona.activo.is_(True)).order_by(ComandaZona.nombre)
                )
            ).scalars()
        )

    async def crear_zona_comanda(self, nombre: str) -> ComandaZona:
        zona = ComandaZona(nombre=nombre)
        self._s.add(zona)
        await self._s.flush()
        return zona

    async def rutear_producto(self, producto_id: int, zona_id: int | None) -> None:
        await self._s.execute(
            text("UPDATE productos SET zona_comanda_id = :z WHERE id = :p"),
            {"z": zona_id, "p": producto_id},
        )

    # --- mesas (F3 / ADR 0032 D4) ----------------------------------------------------
    async def mesa_por_id(self, mesa_id: int) -> Mesa | None:
        return await self._s.get(Mesa, mesa_id)

    async def listar_mesas(self, *, solo_activas: bool = True) -> list[Mesa]:
        consulta = select(Mesa).order_by(Mesa.nombre)
        if solo_activas:
            consulta = consulta.where(Mesa.activo.is_(True))
        return list((await self._s.execute(consulta)).scalars())

    async def crear_mesa(self, *, nombre: str, zona: str | None) -> Mesa:
        mesa = Mesa(nombre=nombre, zona=zona)
        self._s.add(mesa)
        await self._s.flush()
        return mesa

    async def orden_abierta_de(self, mesa_id: int, *, for_update: bool = False) -> Pedido | None:
        """La orden `abierta` de la mesa (una sola, por índice parcial UNIQUE)."""
        consulta = select(Pedido).where(Pedido.mesa_id == mesa_id, Pedido.estado == "abierto")
        if for_update:
            consulta = consulta.with_for_update()
        return (await self._s.execute(consulta)).scalar_one_or_none()

    async def abrir_orden_mesa(self, mesa: Mesa) -> Pedido:
        pedido = Pedido(
            cliente_telefono=f"mesa:{mesa.id}", cliente_nombre=mesa.nombre,
            estado="abierto", origen="mesa", mesa_id=mesa.id,
        )
        self._s.add(pedido)
        await self._s.flush()
        # Los timestamps son server-default y refresh() expira el resto: cargar también `items`
        # (serializar sin esto dispara un lazy-load síncrono → MissingGreenlet).
        await self._s.refresh(pedido, attribute_names=["creado_en", "actualizado_en", "items"])
        await publish(self._s, "mesa_abierta", {"mesa_id": mesa.id, "pedido_id": pedido.id})
        return pedido

    async def agregar_items(self, pedido: Pedido, filas: list[dict]) -> Pedido:
        """APPEND de una ronda a la orden abierta (a diferencia del borrador, que reemplaza)."""
        await self._s.refresh(pedido, attribute_names=["items"])
        nuevos: list[PedidoItem] = []
        for fila in filas:
            item = PedidoItem(**fila)
            pedido.items.append(item)
            nuevos.append(item)
        pedido.subtotal = sum((i.subtotal for i in pedido.items), Decimal("0"))
        pedido.total = pedido.subtotal + pedido.costo_domicilio
        await self._s.flush()
        await self._s.refresh(pedido, attribute_names=["actualizado_en"])
        await publish(self._s, "mesa_items", {
            "mesa_id": pedido.mesa_id, "pedido_id": pedido.id, "total": str(pedido.total),
        })
        # KDS (F4): cada RONDA entra a cocina como su(s) comanda(s) por zona.
        await self.crear_comandas(pedido, nuevos)
        return pedido

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
