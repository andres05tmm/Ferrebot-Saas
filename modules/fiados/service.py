"""Servicio de fiados: dominio (saldo, sobre-abono, idempotencia). SQL en el repositorio.

Las mutaciones serializan con el lock de la fila ancla (cliente al crear, fiado al abonar) y el
chequeo de idempotencia va dentro de esa sección crítica.
"""
from dataclasses import dataclass
from decimal import Decimal

from modules.fiados.errors import ClienteInexistente, FiadoInexistente, SobreAbono
from modules.fiados.models import Fiado, FiadoMovimiento
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.saldo import excede_saldo


@dataclass(frozen=True, slots=True)
class ResultadoFiado:
    fiado: Fiado
    replay: bool


@dataclass(frozen=True, slots=True)
class ResultadoAbono:
    movimiento: FiadoMovimiento
    replay: bool


class FiadosService:
    def __init__(self, repo: SqlFiadosRepository) -> None:
        self._repo = repo

    async def crear(
        self,
        *,
        cliente_id: int,
        venta_id: int | None,
        monto: Decimal,
        idempotency_key: str | None = None,
    ) -> ResultadoFiado:
        cliente = await self._repo.lock_cliente(cliente_id)   # serializa por cliente
        if cliente is None:
            raise ClienteInexistente(cliente_id)
        if idempotency_key:
            previo = await self._repo.fiado_por_key(idempotency_key)
            if previo is not None:
                return ResultadoFiado(previo, replay=True)
        fiado = await self._repo.crear_fiado(
            cliente_id=cliente_id, venta_id=venta_id, monto=monto, idempotency_key=idempotency_key,
        )
        return ResultadoFiado(fiado, replay=False)

    async def abonar(
        self, *, fiado_id: int, monto: Decimal, idempotency_key: str | None = None
    ) -> ResultadoAbono:
        fiado = await self._repo.lock_fiado(fiado_id)          # serializa por fiado
        if fiado is None:
            raise FiadoInexistente(fiado_id)
        if idempotency_key:
            previo = await self._repo.movimiento_por_key(idempotency_key)
            if previo is not None:
                return ResultadoAbono(previo, replay=True)
        saldo = fiado.saldo or Decimal("0")
        if excede_saldo(saldo, monto):
            raise SobreAbono(fiado_id, saldo, monto)
        movimiento = await self._repo.abonar(fiado, monto=monto, idempotency_key=idempotency_key)
        return ResultadoAbono(movimiento, replay=False)

    async def deudas(self) -> list[dict]:
        return await self._repo.deudas()

    async def fiados_de(self, cliente_id: int) -> list[Fiado]:
        """Fiados vivos de un cliente (cliente sin fiados o inexistente → lista vacía, sin 404: es una
        lectura para poblar el modal de abono, no una mutación)."""
        return await self._repo.fiados_con_saldo(cliente_id)
