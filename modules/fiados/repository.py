"""Repositorio de fiados: único lugar con SQL (regla no negociable #2).

`fiados_movimientos` es la fuente de verdad; `fiados.saldo` y `clientes.saldo_fiado` son
contadores denormalizados que se escriben en la MISMA transacción que el movimiento (dual-write
atómico). Cada cargo/abono inserta su fila en el ledger antes de tocar los contadores.
"""
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.clientes.models import Cliente
from modules.fiados.models import Fiado, FiadoMovimiento
from modules.fiados.saldo import ABONO, CARGO, nuevo_saldo


class SqlFiadosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_cliente(self, cliente_id: int) -> Cliente | None:
        return (
            await self._s.execute(
                select(Cliente).where(Cliente.id == cliente_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def lock_fiado(self, fiado_id: int) -> Fiado | None:
        return (
            await self._s.execute(
                select(Fiado).where(Fiado.id == fiado_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def fiado_por_key(self, idempotency_key: str) -> Fiado | None:
        return (
            await self._s.execute(
                select(Fiado).where(Fiado.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def movimiento_por_key(self, idempotency_key: str) -> FiadoMovimiento | None:
        return (
            await self._s.execute(
                select(FiadoMovimiento).where(FiadoMovimiento.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def crear_fiado(
        self,
        *,
        cliente_id: int,
        venta_id: int | None,
        monto: Decimal,
        idempotency_key: str | None,
    ) -> Fiado:
        """Crea el fiado (saldo=monto) + su movimiento cargo + actualiza clientes.saldo_fiado."""
        fiado = Fiado(
            cliente_id=cliente_id, venta_id=venta_id, monto=monto, saldo=monto,
            idempotency_key=idempotency_key,
        )
        self._s.add(fiado)
        await self._s.flush()   # asigna fiado.id
        # Cargo en el ledger (sin key: el ancla idempotente es la fila `fiados`).
        self._s.add(FiadoMovimiento(fiado_id=fiado.id, tipo=CARGO, monto=monto))
        await self._s.execute(
            text("UPDATE clientes SET saldo_fiado = saldo_fiado + :m WHERE id = :cid"),
            {"m": monto, "cid": cliente_id},
        )
        await self._s.flush()
        await publish(self._s, "fiado_registrado", {
            "fiado_id": fiado.id, "cliente_id": cliente_id, "monto": str(monto),
        })
        return fiado

    async def abonar(self, fiado: Fiado, *, monto: Decimal, idempotency_key: str | None) -> FiadoMovimiento:
        """Inserta el abono en el ledger y actualiza fiado.saldo + clientes.saldo_fiado."""
        movimiento = FiadoMovimiento(
            fiado_id=fiado.id, tipo=ABONO, monto=monto, idempotency_key=idempotency_key,
        )
        self._s.add(movimiento)
        fiado.saldo = nuevo_saldo(fiado.saldo or Decimal("0"), ABONO, monto)
        await self._s.execute(
            text("UPDATE clientes SET saldo_fiado = saldo_fiado - :m WHERE id = :cid"),
            {"m": monto, "cid": fiado.cliente_id},
        )
        await self._s.flush()
        await publish(self._s, "fiado_abonado", {
            "fiado_id": fiado.id, "movimiento_id": movimiento.id,
            "monto": str(monto), "saldo": str(fiado.saldo),
        })
        return movimiento

    async def deudas(self) -> list[dict]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id AS cliente_id, nombre, saldo_fiado FROM clientes "
                    "WHERE saldo_fiado > 0 ORDER BY saldo_fiado DESC"
                )
            )
        ).all()
        return [dict(row._mapping) for row in rows]
