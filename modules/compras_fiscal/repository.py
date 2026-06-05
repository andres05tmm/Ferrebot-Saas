"""Repositorio de compras fiscales: único lugar con SQL del módulo (regla no negociable #2).

Inserta el desglose de IVA cuantizado a centavos; `creado_en` se fija en hora Colombia (regla #4). La
derivación desde una compra normal toma el total de la compra (base/iva en 0: el desglose no se conoce)
y es idempotente por `compra_id`. Todo corre en la transacción de la sesión del tenant.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.money import cuantizar
from modules.compras.models import Compra
from modules.compras_fiscal.models import CompraFiscal
from modules.compras_fiscal.schemas import CompraFiscalLeer


class SqlComprasFiscalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def crear(
        self,
        *,
        proveedor_nit: str | None,
        base: Decimal,
        iva: Decimal,
        total: Decimal,
        soporte_url: str | None = None,
        compra_id: int | None = None,
    ) -> CompraFiscalLeer:
        """Inserta una compra fiscal (montos cuantizados; `creado_en` en hora Colombia)."""
        orm = CompraFiscal(
            compra_id=compra_id,
            proveedor_nit=proveedor_nit,
            base=cuantizar(base),
            iva=cuantizar(iva),
            total=cuantizar(total),
            soporte_url=soporte_url,
            creado_en=now_co(),
        )
        self._s.add(orm)
        await self._s.flush()
        return CompraFiscalLeer.model_validate(orm)

    async def listar(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> list[CompraFiscalLeer]:
        """Compras fiscales del rango por `creado_en` (el servicio resuelve el default mes), recientes primero."""
        stmt = select(CompraFiscal)
        if inicio is not None:
            stmt = stmt.where(CompraFiscal.creado_en >= inicio)
        if fin is not None:
            stmt = stmt.where(CompraFiscal.creado_en <= fin)
        stmt = stmt.order_by(CompraFiscal.id.desc())
        filas = (await self._s.execute(stmt)).scalars().all()
        return [CompraFiscalLeer.model_validate(f) for f in filas]

    async def fiscal_por_compra(self, compra_id: int) -> CompraFiscalLeer | None:
        """La compra fiscal ya ligada a esa compra, o None (base de la idempotencia de to-fiscal)."""
        orm = (
            await self._s.execute(
                select(CompraFiscal)
                .where(CompraFiscal.compra_id == compra_id)
                .order_by(CompraFiscal.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return CompraFiscalLeer.model_validate(orm) if orm is not None else None

    async def total_de_compra(self, compra_id: int) -> Decimal | None:
        """Total de la compra normal; None si la compra no existe (la fila sin total cuenta como 0)."""
        fila = (
            await self._s.execute(
                select(Compra.id, Compra.total).where(Compra.id == compra_id)
            )
        ).first()
        if fila is None:
            return None
        return Decimal(fila.total) if fila.total is not None else Decimal("0")
