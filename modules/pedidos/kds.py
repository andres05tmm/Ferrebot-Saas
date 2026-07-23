"""KDS — vista de cocina (F4 Pack Restaurante, ADR 0032 D5).

Las comandas se GENERAN en el repositorio al confirmar el pedido / por ronda de mesa (una por
zona). Aquí vive el ciclo de la comanda: transiciones válidas y auditadas (timestamps) + el aviso
al canal del pedido cuando TODAS sus comandas están listas.

`notificar_listo` es un PUERTO (async callable que recibe el Pedido): el adaptador de canal real
(WhatsApp/Telegram) lo cablea; los tests lo mockean; None = solo el evento SSE `pedido_listo`.
Best-effort: un fallo del canal jamás rompe la operación de cocina.
"""
from collections.abc import Awaitable, Callable

from core.events import publish
from core.logging import get_logger
from modules.pedidos.models import Comanda, ComandaZona, Pedido, TRANSICIONES_COMANDA
from modules.pedidos.repository import SqlPedidosRepository

log = get_logger("pedidos.kds")

Notificador = Callable[[Pedido], Awaitable[None]]


class ComandaInexistente(Exception):
    """La comanda no existe."""


class TransicionComandaInvalida(Exception):
    """Cambio de estado fuera del ciclo pendiente → en_preparacion → listo."""

    def __init__(self, actual: str, nuevo: str) -> None:
        super().__init__(f"{actual} → {nuevo}")
        self.actual = actual
        self.nuevo = nuevo


class KdsService:
    def __init__(
        self, repo: SqlPedidosRepository, *, notificar_listo: Notificador | None = None
    ) -> None:
        self._repo = repo
        self._notificar = notificar_listo

    async def listar(self, *, estados: list[str] | None = None) -> list[Comanda]:
        """Cola de comandas (default: las activas — pendiente y en preparación)."""
        return await self._repo.listar_comandas(
            estados=estados or ["pendiente", "en_preparacion"]
        )

    async def listar_zonas(self) -> list[ComandaZona]:
        return await self._repo.listar_zonas_comanda()

    async def crear_zona(self, nombre: str) -> ComandaZona:
        return await self._repo.crear_zona_comanda(nombre)

    async def rutear(self, producto_id: int, zona_id: int | None) -> None:
        await self._repo.rutear_producto(producto_id, zona_id)

    async def cambiar_estado(self, comanda_id: int, nuevo: str) -> Comanda:
        """Avanza la comanda (auditado). Si con esto TODAS las del pedido quedan listas → aviso."""
        comanda = await self._repo.comanda_por_id(comanda_id)
        if comanda is None:
            raise ComandaInexistente()
        if nuevo not in TRANSICIONES_COMANDA.get(comanda.estado, frozenset()):
            raise TransicionComandaInvalida(comanda.estado, nuevo)
        comanda = await self._repo.avanzar_comanda(comanda, nuevo)
        if nuevo == "listo":
            hermanas = await self._repo.comandas_de_pedido(comanda.pedido_id)
            if all(c.estado == "listo" for c in hermanas):
                await self._avisar_pedido_listo(comanda.pedido_id)
        return comanda

    async def _avisar_pedido_listo(self, pedido_id: int) -> None:
        pedido = await self._repo.pedido_por_id(pedido_id)
        if pedido is None:
            return
        await publish(self._repo.sesion, "pedido_listo", {
            "pedido_id": pedido.id, "telefono": pedido.telefono_contacto or pedido.cliente_telefono,
        })
        if self._notificar is not None:
            try:
                await self._notificar(pedido)
            except Exception:  # noqa: BLE001 — el canal jamás rompe la cocina
                log.warning("kds_notificacion_fallo", pedido_id=pedido.id, exc_info=True)
