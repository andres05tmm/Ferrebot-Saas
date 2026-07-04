"""Asiento de apertura desde un corte de saldos iniciales (ADR 0030, cabo a).

Dado un corte (inventario a costo promedio, cartera/fiado, caja, bancos, CxP), arma UN asiento
balanceado: los activos al débito, los proveedores al crédito y el patrimonio como partida de cierre
que cuadra el asiento (activos − pasivos). Idempotente por período: la `idempotency_key`
`apertura:{anio}-{mes}` garantiza un solo asiento de apertura por período/tenant (reintento → replay).
"""
from __future__ import annotations

from decimal import Decimal

from core.logging import get_logger
from core.money import cuantizar
from modules.contabilidad import puc_seed as puc
from modules.contabilidad.errors import CorteVacio
from modules.contabilidad.ledger import LedgerService, ResultadoAsiento
from modules.contabilidad.repository import SqlContabilidadRepository
from modules.contabilidad.schemas import AsientoCrear, CorteApertura, LineaAsiento

log = get_logger("contabilidad.apertura")

CERO = Decimal("0")


class AperturaService:
    def __init__(self, ledger: LedgerService, repo: SqlContabilidadRepository) -> None:
        self._ledger = ledger
        self._repo = repo

    async def registrar_apertura(self, corte: CorteApertura) -> ResultadoAsiento:
        """Postea el asiento de apertura del período de `corte.fecha`. Idempotente por período."""
        await self._repo.asegurar_puc()

        activos: list[tuple[str, Decimal]] = [
            (puc.CAJA, cuantizar(corte.caja)),
            (puc.BANCOS, cuantizar(corte.bancos)),
            (puc.CLIENTES, cuantizar(corte.cartera)),
            (puc.INVENTARIO, cuantizar(corte.inventario)),
        ]
        cxp = cuantizar(corte.cuentas_por_pagar)

        lineas: list[LineaAsiento] = [
            LineaAsiento(cuenta_codigo=codigo, direction="debit", amount=monto,
                         descripcion="Saldo inicial")
            for codigo, monto in activos if monto > 0
        ]
        if cxp > 0:
            lineas.append(
                LineaAsiento(cuenta_codigo=puc.PROVEEDORES, direction="credit", amount=cxp,
                             descripcion="Cuentas por pagar iniciales")
            )

        total_activos = cuantizar(sum((m for _, m in activos), CERO))
        patrimonio = cuantizar(total_activos - cxp)
        if patrimonio > 0:
            lineas.append(
                LineaAsiento(cuenta_codigo=puc.PATRIMONIO, direction="credit", amount=patrimonio,
                             descripcion="Patrimonio inicial (partida de cierre)")
            )
        elif patrimonio < 0:
            lineas.append(
                LineaAsiento(cuenta_codigo=puc.PATRIMONIO, direction="debit", amount=-patrimonio,
                             descripcion="Patrimonio inicial (partida de cierre)")
            )

        if not lineas:
            raise CorteVacio("el corte de apertura no tiene saldos: no hay nada que asentar")

        key = f"apertura:{corte.fecha.year}-{corte.fecha.month:02d}"
        res = await self._ledger.registrar_asiento(
            AsientoCrear(
                fecha=corte.fecha, origen_tipo="apertura", origen_id=None,
                descripcion=f"Apertura {corte.fecha.year}-{corte.fecha.month:02d}",
                idempotency_key=key, lineas=lineas,
            )
        )
        if not res.replay:
            log.info("asiento_apertura", entry_id=res.entry.id, patrimonio=str(patrimonio))
        return res
