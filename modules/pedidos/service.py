"""Motor del pack pedidos (ADR 0016): determinista, igual para todos los tenants.

El agente NUNCA resuelve productos, precios ni tarifas de domicilio: aquí viven el horario de
cocina, la resolución contra el catálogo real (buscador de 4 capas), la validación de stock
(informativa: el pedido no descuenta stock — regla #7), el mínimo de pedido, la tarifa por zona y
las transiciones del ciclo. Identidad del cliente = el teléfono que escribe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from modules.pedidos.errors import (
    CocinaCerrada,
    ModificadorInvalido,
    ModificadorNoEncontrado,
    PedidoInexistente,
    PedidoMuyChico,
    ProductoNoEncontrado,
    SinBorrador,
    StockInsuficiente,
    TransicionInvalida,
)
from modules.pedidos.models import TRANSICIONES, Pedido
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.schemas import PedidoConfigActualizar, ZonaCrear


@dataclass(frozen=True, slots=True)
class ItemPedido:
    """Un ítem como lo pide el cliente: nombre libre + cantidad (+ modificadores dichos).

    Los `modificadores` son texto libre del cliente ("sin cebolla"); el motor los resuelve contra
    las opciones del producto — jamás se inventan (F2 / ADR 0032 D3).
    """

    producto: str
    cantidad: Decimal
    modificadores: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResultadoArmar:
    """Borrador armado. `replay=True` si la idempotency_key ya existía (no se duplicó)."""

    pedido: Pedido
    replay: bool


class PedidosService:
    def __init__(self, repo: SqlPedidosRepository) -> None:
        self._repo = repo

    # --- menú (solo lectura) ----------------------------------------------------
    async def ver_menu(self, buscar: str = "", *, limite: int = 20) -> list[dict]:
        """Productos del catálogo para ofrecer: lista general o búsqueda con el buscador real."""
        if not buscar.strip():
            return await self._repo.menu(limite=limite)
        resultado = await self._repo.buscar_producto(buscar, limite=limite)
        menu: list[dict] = []
        for c in resultado.coincidencias:
            fila = await self._repo.producto_para_menu(c.producto_id)
            if fila is not None:
                menu.append(fila)
        return menu

    # --- armar / confirmar (cara al cliente, acotado al teléfono) -----------------
    async def armar_pedido(
        self,
        telefono: str,
        items: list[ItemPedido],
        *,
        ahora: datetime,
        notas: str | None = None,
        idempotency_key: str | None = None,
        origen: str = "whatsapp",
    ) -> ResultadoArmar:
        """Resuelve los ítems contra el catálogo y deja UN borrador (`recibido`) por teléfono.

        Volver a armar reemplaza el borrador (el cliente cambió de opinión). Valida horario de
        cocina y stock disponible (sin descontarlo). Idempotente por `idempotency_key`.
        """
        if idempotency_key:
            existente = await self._repo.pedido_por_key(idempotency_key)
            if existente is not None:
                return ResultadoArmar(pedido=existente, replay=True)

        config = await self._repo.obtener_config()
        if not config.activo or not (config.hora_apertura <= ahora.time() < config.hora_cierre):
            raise CocinaCerrada()

        filas = await self.resolver_items(items)

        pedido = await self._repo.borrador_de(telefono)
        if pedido is None:
            pedido = await self._repo.crear_pedido(
                telefono=telefono, notas=notas, idempotency_key=idempotency_key, origen=origen
            )
        elif notas:
            pedido.notas = notas
        pedido = await self._repo.reemplazar_items(pedido, filas)
        return ResultadoArmar(pedido=pedido, replay=False)

    async def resolver_items(self, items: list[ItemPedido]) -> list[dict]:
        """Resuelve ítems de texto libre contra el catálogo real (buscador de 4 capas) + modificadores.

        Devuelve las filas snapshot ({producto_id, nombre, cantidad, precio_unitario, subtotal,
        modificadores}). Lo comparten el domicilio (`armar_pedido`) y el salón (`MesasService`):
        el agente y el mesero jamás inventan productos ni precios.
        """
        filas: list[dict] = []
        for item in items:
            resultado = await self._repo.buscar_producto(item.producto)
            resuelto = next((c for c in resultado.coincidencias if not c.sugerencia), None)
            if resuelto is None:
                sugerencias = [c.nombre for c in resultado.coincidencias][:3]
                raise ProductoNoEncontrado(item.producto, sugerencias)
            fila = await self._repo.producto_para_menu(resuelto.producto_id)
            if fila is None:
                raise ProductoNoEncontrado(item.producto, [])
            if Decimal(fila["stock"]) < item.cantidad:
                raise StockInsuficiente(fila["nombre"], fila["stock"])
            precio = Decimal(fila["precio_venta"])
            snapshot = await self._resolver_modificadores(
                fila["id"], fila["nombre"], item.modificadores
            )
            precio += sum((Decimal(m["delta_precio"]) for m in snapshot), Decimal("0"))
            filas.append({
                "producto_id": fila["id"], "nombre": fila["nombre"], "cantidad": item.cantidad,
                "precio_unitario": precio, "subtotal": precio * item.cantidad,
                "modificadores": snapshot or None,
            })
        return filas

    async def _resolver_modificadores(
        self, producto_id: int, producto_nombre: str, pedidos: tuple[str, ...]
    ) -> list[dict]:
        """Resuelve los modificadores dichos contra el catálogo del producto y valida min/max.

        Devuelve el SNAPSHOT [{grupo, opcion, delta_precio}] en el orden dicho. Anti-alucinación:
        texto que no matchea una opción activa → `ModificadorNoEncontrado` con sugerencias; grupo
        obligatorio/min sin cubrir o max excedido → `ModificadorInvalido` (el bot pregunta).
        """
        grupos = await self._repo.modificadores_de(producto_id)
        if not grupos and not pedidos:
            return []
        # (opción activa) → (grupo, opción); match por nombre normalizado.
        indice: dict[str, tuple] = {}
        for g in grupos:
            for o in g.opciones:
                if o.activo:
                    indice[o.nombre.strip().lower()] = (g, o)
        if pedidos and not indice:
            raise ModificadorNoEncontrado(pedidos[0], producto_nombre, [])

        snapshot: list[dict] = []
        conteo: dict[int, int] = {}
        for dicho in pedidos:
            clave = dicho.strip().lower()
            par = indice.get(clave)
            if par is None:
                # Match laxo: contención en cualquiera de las dos direcciones ("queso" ~ "Adición de queso").
                candidatos = [v for k, v in indice.items() if clave in k or k in clave]
                par = candidatos[0] if len(candidatos) == 1 else None
            if par is None:
                sugerencias = [o.nombre for g in grupos for o in g.opciones if o.activo][:8]
                raise ModificadorNoEncontrado(dicho, producto_nombre, sugerencias)
            grupo, opcion = par
            conteo[grupo.id] = conteo.get(grupo.id, 0) + 1
            snapshot.append({
                "grupo": grupo.nombre, "opcion": opcion.nombre,
                "delta_precio": f"{opcion.delta_precio:.2f}",
            })
        for g in grupos:
            n = conteo.get(g.id, 0)
            minimo = max(g.min_sel, 1 if g.obligatorio else 0)
            opciones = [o.nombre for o in g.opciones if o.activo]
            if n < minimo:
                raise ModificadorInvalido(
                    producto_nombre,
                    f"'{producto_nombre}' requiere elegir {minimo} de {g.nombre}.",
                    opciones,
                )
            if g.max_sel is not None and n > g.max_sel:
                raise ModificadorInvalido(
                    producto_nombre,
                    f"'{g.nombre}' de '{producto_nombre}' admite máximo {g.max_sel}.",
                    opciones,
                )
        return snapshot

    async def confirmar_pedido(
        self,
        telefono: str,
        *,
        direccion: str,
        barrio: str = "",
        metodo_pago: str,
        nombre: str | None = None,
        telefono_contacto: str | None = None,
    ) -> tuple[Pedido, int]:
        """Confirma el borrador del que escribe: mínimo de pedido + tarifa por zona (o default).

        Devuelve (pedido, tiempo_estimado_min). Emite SSE — la cocina lo ve al instante.
        """
        pedido = await self._repo.borrador_de(telefono)
        if pedido is None:
            raise SinBorrador()
        config = await self._repo.obtener_config()
        if pedido.subtotal < config.minimo_pedido:
            raise PedidoMuyChico(config.minimo_pedido)
        zona = await self._repo.zona_por_nombre(barrio) if barrio.strip() else None
        costo = zona.tarifa if zona is not None else config.costo_domicilio_default
        pedido = await self._repo.confirmar(
            pedido, direccion=direccion, zona_id=zona.id if zona else None,
            costo_domicilio=costo, metodo_pago=metodo_pago, nombre=nombre,
            telefono_contacto=telefono_contacto,
        )
        return pedido, config.tiempo_estimado_min

    async def estado_de(self, telefono: str) -> Pedido | None:
        """El último pedido del que escribe — jamás el de otro teléfono."""
        return await self._repo.ultimo_de(telefono)

    # --- dashboard (kanban) ---------------------------------------------------------
    async def listar(self, *, estados: list[str] | None = None) -> list[Pedido]:
        return await self._repo.listar(estados=estados)

    async def cambiar_estado(self, pedido_id: int, nuevo: str) -> Pedido:
        pedido = await self._repo.pedido_por_id(pedido_id)
        if pedido is None:
            raise PedidoInexistente(str(pedido_id))
        if nuevo not in TRANSICIONES.get(pedido.estado, frozenset()):
            raise TransicionInvalida(pedido.estado, nuevo)
        return await self._repo.cambiar_estado(pedido, nuevo)

    async def obtener_config(self):
        return await self._repo.obtener_config()

    async def guardar_config(self, datos: PedidoConfigActualizar):
        return await self._repo.guardar_config(datos)

    async def listar_zonas(self, *, solo_activas: bool = True):
        return await self._repo.listar_zonas(solo_activas=solo_activas)

    async def crear_zona(self, datos: ZonaCrear):
        return await self._repo.crear_zona(datos)

    async def desactivar_zona(self, zona_id: int) -> None:
        zona = await self._repo.zona_por_id(zona_id)
        if zona is not None:
            zona.activo = False
