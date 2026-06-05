"""Servicio de compras fiscales: orquesta el registro, el listado por rango y la derivación desde compra.

Lógica de dominio (sin SQL): resuelve el rango por defecto (mes en curso, hora Colombia, regla #4) y
gobierna la idempotencia de `desde_compra` (si ya hay una fiscal ligada a la compra, la devuelve sin
crear otra). La coherencia de montos del POST la valida el schema (`CompraFiscalCrear`).
"""
from datetime import date, datetime
from decimal import Decimal

from core.config.timezone import rango_dia_co, today_co
from modules.compras_fiscal.errors import CompraInexistente
from modules.compras_fiscal.repository import SqlComprasFiscalRepository
from modules.compras_fiscal.schemas import CompraFiscalCrear, CompraFiscalLeer


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[datetime, datetime]:
    """Ventana [inicio, fin] aware: rango dado o, si falta, el mes en curso (día 1 → hoy Colombia)."""
    hoy = today_co()
    return rango_dia_co(desde or hoy.replace(day=1), hasta or hoy)


class ComprasFiscalService:
    def __init__(self, repo: SqlComprasFiscalRepository) -> None:
        self._repo = repo

    async def registrar(self, datos: CompraFiscalCrear) -> CompraFiscalLeer:
        """Registra una compra fiscal con su desglose de IVA ya validado por el schema."""
        return await self._repo.crear(
            proveedor_nit=datos.proveedor_nit, base=datos.base, iva=datos.iva,
            total=datos.total, soporte_url=datos.soporte_url, compra_id=datos.compra_id,
        )

    async def listar(self, *, desde: date | None, hasta: date | None) -> list[CompraFiscalLeer]:
        """Compras fiscales del rango (default mes en curso, hora Colombia)."""
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.listar(inicio=inicio, fin=fin)

    async def desde_compra(self, compra_id: int) -> tuple[CompraFiscalLeer, bool]:
        """Deriva una compra fiscal de una compra normal. Devuelve `(fiscal, creada)`.

        Idempotente: si ya hay una fiscal ligada a esa compra, la devuelve (`creada=False`). El total
        sale de la compra; base/iva quedan en 0 (el desglose no se conoce aquí). 404 si no existe.
        """
        existente = await self._repo.fiscal_por_compra(compra_id)
        if existente is not None:
            return existente, False
        total = await self._repo.total_de_compra(compra_id)
        if total is None:
            raise CompraInexistente(compra_id)
        creada = await self._repo.crear(
            proveedor_nit=None, base=Decimal("0"), iva=Decimal("0"),
            total=total, compra_id=compra_id,
        )
        return creada, True
