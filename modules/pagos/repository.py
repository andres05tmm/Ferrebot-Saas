"""Repositorio de cobros: único lugar con SQL del frente de pagos (regla #2)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.pagos.models import Cobro, Comprobante


class SqlPagosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @property
    def session(self) -> AsyncSession:
        """La sesión del tenant (para emitir eventos de la cascada compartida desde el servicio)."""
        return self._s

    async def cobros_pedido_pendientes_por_monto(
        self, monto: Decimal, *, desde: datetime
    ) -> list[Cobro]:
        """Cobros `pendiente` de origen `pedido` con monto EXACTO, creados desde `desde` (ventana).

        Es la consulta del conciliador de transferencias: casa una transferencia entrante contra el
        cobro del pedido por monto exacto dentro de la ventana temporal. La regla del candidato único
        la aplica el conciliador (aquí solo se listan)."""
        return list(
            (
                await self._s.execute(
                    select(Cobro).where(
                        Cobro.origen == "pedido",
                        Cobro.estado == "pendiente",
                        Cobro.monto == monto,
                        Cobro.creado_en >= desde,
                    )
                )
            ).scalars()
        )

    async def cobros_pedido_pendientes_de_cliente(
        self, cliente_telefono: str, *, desde: datetime
    ) -> list[Cobro]:
        """Cobros `pendiente` de origen `pedido` de ESE cliente, creados desde `desde` (ventana).

        Es la consulta del registro de comprobantes: casar la foto del cliente contra SUS pedidos
        pendientes (el desempate por monto lo hace `comprobantes.registrar_comprobante`)."""
        return list(
            (
                await self._s.execute(
                    select(Cobro).where(
                        Cobro.origen == "pedido",
                        Cobro.estado == "pendiente",
                        Cobro.cliente_telefono == cliente_telefono,
                        Cobro.creado_en >= desde,
                    )
                )
            ).scalars()
        )

    async def crear_comprobante(self, comprobante: Comprobante) -> Comprobante:
        """Inserta la fila de auditoría del comprobante (siempre se guarda, casó o no)."""
        self._s.add(comprobante)
        await self._s.flush()
        return comprobante

    async def cobro_ids_con_comprobante(self, cobro_ids: list[int]) -> set[int]:
        """De `cobro_ids`, cuáles tienen ≥1 comprobante asociado. Una sola consulta (sin N+1).

        El desempate del conciliador: entre varios candidatos por monto, el que tiene comprobante."""
        if not cobro_ids:
            return set()
        filas = (
            await self._s.execute(
                select(Comprobante.cobro_id)
                .where(Comprobante.cobro_id.in_(cobro_ids))
                .distinct()
            )
        ).scalars()
        return {cid for cid in filas if cid is not None}

    async def cobro_por_referencia(self, referencia: str) -> Cobro | None:
        return (
            await self._s.execute(select(Cobro).where(Cobro.referencia == referencia))
        ).scalar_one_or_none()

    async def cobro_por_origen(self, origen: str, origen_id: int) -> Cobro | None:
        return (
            await self._s.execute(
                select(Cobro).where(Cobro.origen == origen, Cobro.origen_id == origen_id)
            )
        ).scalar_one_or_none()

    async def cobro_por_id(self, cobro_id: int) -> Cobro | None:
        return await self._s.get(Cobro, cobro_id)

    async def crear(self, cobro: Cobro) -> Cobro:
        self._s.add(cobro)
        await self._s.flush()
        await publish(self._s, "cobro_creado", {
            "cobro_id": cobro.id, "origen": cobro.origen, "monto": str(cobro.monto),
        })
        return cobro

    async def marcar(self, cobro: Cobro, estado: str) -> Cobro:
        cobro.estado = estado
        await self._s.flush()
        await self._s.refresh(cobro, attribute_names=["actualizado_en"])   # onupdate lo expiró
        evento = "cobro_pagado" if estado == "pagado" else "cobro_estado"
        await publish(self._s, evento, {"cobro_id": cobro.id, "estado": estado})
        return cobro

    async def pendientes_de(self, proveedor: str, *, limite: int = 100) -> list[Cobro]:
        """Cobros del proveedor aún pendientes (los barre la conciliación del worker)."""
        return list(
            (
                await self._s.execute(
                    select(Cobro)
                    .where(Cobro.estado == "pendiente", Cobro.proveedor == proveedor)
                    .order_by(Cobro.id)
                    .limit(limite)
                )
            ).scalars()
        )

    async def listar(self, *, estados: list[str] | None = None, limite: int = 200) -> list[Cobro]:
        consulta = select(Cobro).order_by(Cobro.creado_en.desc()).limit(limite)
        if estados:
            consulta = consulta.where(Cobro.estado.in_(estados))
        return list((await self._s.execute(consulta)).scalars())
