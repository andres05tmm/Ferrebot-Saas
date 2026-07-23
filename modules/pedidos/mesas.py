"""Mesas y salón (F3 Pack Restaurante, ADR 0032 D4): orden abierta por mesa sobre `pedidos`.

La orden de mesa ES un `Pedido` con `origen='mesa'` y estado `abierto`: ítems por rondas (APPEND
bajo FOR UPDATE — dos meseros concurrentes no se pisan), precuenta de solo lectura y cobro por el
puente F1 (`convertir_pedido`) con propina opcional. El módulo es aparte de `service.py`: el salón
es otra cara del mismo motor (reusa `resolver_items` — el mesero tampoco inventa precios).
"""
from decimal import Decimal

from modules.pedidos.conversion import ResultadoConversion, convertir_pedido
from modules.pedidos.models import Mesa, Pedido
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService
from modules.ventas.service import VentaService


class MesaInexistente(Exception):
    """La mesa no existe o está inactiva."""


class MesaSinOrden(Exception):
    """La mesa no tiene una orden abierta (precuenta/agregar/cobrar requieren una)."""


class MesasService:
    def __init__(self, repo: SqlPedidosRepository) -> None:
        self._repo = repo
        self._pedidos = PedidosService(repo)   # reusa resolver_items (catálogo + modificadores)

    async def listar(self) -> list[tuple[Mesa, Pedido | None]]:
        """Mesas activas con su orden abierta (o None): la grilla del salón."""
        mesas = await self._repo.listar_mesas()
        return [(m, await self._repo.orden_abierta_de(m.id)) for m in mesas]

    async def crear(self, *, nombre: str, zona: str | None = None) -> Mesa:
        return await self._repo.crear_mesa(nombre=nombre, zona=zona)

    async def desactivar(self, mesa_id: int) -> None:
        mesa = await self._repo.mesa_por_id(mesa_id)
        if mesa is not None:
            mesa.activo = False

    async def abrir(self, mesa_id: int) -> Pedido:
        """Abre la mesa (crea la orden). IDEMPOTENTE: mesa ya abierta → la misma orden."""
        mesa = await self._repo.mesa_por_id(mesa_id)
        if mesa is None or not mesa.activo:
            raise MesaInexistente()
        existente = await self._repo.orden_abierta_de(mesa_id)
        if existente is not None:
            return existente
        return await self._repo.abrir_orden_mesa(mesa)

    async def agregar(self, mesa_id: int, items: list[ItemPedido]) -> Pedido:
        """Agrega una RONDA a la orden abierta (append). FOR UPDATE: meseros concurrentes no se pisan."""
        if await self._repo.mesa_por_id(mesa_id) is None:
            raise MesaInexistente()
        pedido = await self._repo.orden_abierta_de(mesa_id, for_update=True)
        if pedido is None:
            raise MesaSinOrden()
        filas = await self._pedidos.resolver_items(items)
        return await self._repo.agregar_items(pedido, filas)

    async def precuenta(self, mesa_id: int) -> Pedido:
        """La orden abierta con total en vivo (solo lectura; imprimible/compartible en el dashboard)."""
        pedido = await self._repo.orden_abierta_de(mesa_id)
        if pedido is None:
            raise MesaSinOrden()
        return pedido

    async def cobrar(
        self,
        mesa_id: int,
        *,
        ventas: VentaService,
        usuario_id: int,
        metodo_pago: str,
        propina: Decimal | None = None,
        control_stock_estricto: bool = False,
    ) -> ResultadoConversion:
        """Cobra la mesa por el puente F1: venta idempotente + mesa liberada (orden a `entregado`).

        La propina (opcional, elegida por el cliente — decisión de Andrés) va como línea varia
        discriminada; SOLO existe aquí, jamás en domicilio (guardarraíl en `convertir_pedido`).
        """
        pedido = await self._repo.orden_abierta_de(mesa_id)
        if pedido is None:
            raise MesaSinOrden()
        return await convertir_pedido(
            pedido.id, repo=self._repo, ventas=ventas, usuario_id=usuario_id,
            metodo_pago=metodo_pago, propina=propina,
            control_stock_estricto=control_stock_estricto,
        )
