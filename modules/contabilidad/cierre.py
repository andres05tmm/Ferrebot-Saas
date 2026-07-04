"""Cierre formal de un período contable (ADR 0030, cabo b).

Al cerrar un `periodo_contable` se saldan las cuentas de resultado (clases 4/5/6) contra el patrimonio
(resultado del ejercicio): se postea UN asiento que netea a cero cada cuenta de resultado del período y
lleva la utilidad (o pérdida) a Patrimonio. Luego el período pasa a `closed` y ya no admite asientos
(guard en `LedgerService.registrar_asiento`). El asiento de cierre es inmutable como cualquier otro: se
corrige con un espejo (`reversar`), nunca editando.

Idempotente por período: la `idempotency_key` `cierre:{anio}-{mes}` garantiza un solo asiento de cierre
(reintento → replay). Como el ledger valida la idempotencia ANTES del candado de período, reintentar el
cierre sobre un período ya `closed` devuelve el asiento existente sin error.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.config.timezone import COLOMBIA_TZ, now_co
from core.logging import get_logger
from core.money import cuantizar
from modules.contabilidad import puc_seed as puc
from modules.contabilidad.errors import PeriodoBloqueado, PeriodoInexistente
from modules.contabilidad.ledger import LedgerService
from modules.contabilidad.models import JournalEntry
from modules.contabilidad.repository import AgregadoCuenta, SqlContabilidadRepository
from modules.contabilidad.schemas import AsientoCrear, LineaAsiento

log = get_logger("contabilidad.cierre")

CERO = Decimal("0")
_CLASES_RESULTADO = ("4", "5", "6")


@dataclass(frozen=True, slots=True)
class ResultadoCierre:
    entry: JournalEntry | None   # None si el período no tenía movimientos de resultado que saldar
    replay: bool
    utilidad: Decimal


def _fin_de_mes(anio: int, mes: int) -> datetime:
    """Último instante del mes en hora Colombia: fija el asiento de cierre dentro del período."""
    ultimo = calendar.monthrange(anio, mes)[1]
    return datetime(anio, mes, ultimo, 23, 59, 59, tzinfo=COLOMBIA_TZ)


def _lineas_cierre(aggs: list[AgregadoCuenta]) -> tuple[list[LineaAsiento], Decimal]:
    """Arma las líneas que saldan 4/5/6 y devuelve (líneas incl. patrimonio, utilidad del ejercicio).

    Para cada cuenta de resultado se postea la dirección OPUESTA a su saldo (netea a cero). El
    `resultado` acumulado (Σ débitos − Σ créditos de las cuentas de resultado) se lleva a Patrimonio:
    resultado neto al crédito → utilidad; al débito → pérdida.
    """
    lineas: list[LineaAsiento] = []
    resultado = CERO
    for a in aggs:
        if a.codigo[:1] not in _CLASES_RESULTADO:
            continue
        neto = cuantizar(a.debitos - a.creditos)   # >0 saldo débito, <0 saldo crédito
        if neto > 0:
            lineas.append(LineaAsiento(cuenta_codigo=a.codigo, direction="credit", amount=neto,
                                       descripcion="Cierre de resultados"))
        elif neto < 0:
            lineas.append(LineaAsiento(cuenta_codigo=a.codigo, direction="debit", amount=-neto,
                                       descripcion="Cierre de resultados"))
        resultado += neto

    utilidad = cuantizar(-resultado)   # crédito de resultados neto = utilidad
    if resultado < 0:                  # resultados netos al crédito → utilidad → crédito patrimonio
        lineas.append(LineaAsiento(cuenta_codigo=puc.PATRIMONIO, direction="credit", amount=-resultado,
                                   descripcion="Utilidad del ejercicio"))
    elif resultado > 0:                # resultados netos al débito → pérdida → débito patrimonio
        lineas.append(LineaAsiento(cuenta_codigo=puc.PATRIMONIO, direction="debit", amount=resultado,
                                   descripcion="Pérdida del ejercicio"))
    return lineas, utilidad


class CierreService:
    def __init__(self, ledger: LedgerService, repo: SqlContabilidadRepository) -> None:
        self._ledger = ledger
        self._repo = repo

    async def cerrar_periodo(self, anio: int, mes: int) -> ResultadoCierre:
        """Salda 4/5/6 contra patrimonio y cierra el período. Idempotente por período."""
        await self._repo.asegurar_puc()
        periodo = await self._repo.periodo_de(anio, mes)
        if periodo is None:
            raise PeriodoInexistente(f"no existe período {anio}-{mes:02d}")

        key = f"cierre:{anio}-{mes:02d}"
        ahora = now_co()
        previo = await self._repo.asiento_por_idempotency(key)
        if previo is not None:
            if periodo.estado != "closed":
                await self._repo.marcar_periodo(periodo, "closed", ahora=ahora)
            return ResultadoCierre(entry=previo, replay=True, utilidad=CERO)

        if periodo.estado != "open":
            raise PeriodoBloqueado(
                f"período {anio}-{mes:02d} está {periodo.estado}: no se puede cerrar"
            )

        aggs = await self._repo.agregado_por_cuenta_periodo(periodo.id)
        lineas, utilidad = _lineas_cierre(aggs)
        if not lineas:
            # Sin movimientos de resultado: no hay asiento que postear, pero el período se cierra.
            await self._repo.marcar_periodo(periodo, "closed", ahora=ahora)
            log.info("cierre_sin_resultado", anio=anio, mes=mes)
            return ResultadoCierre(entry=None, replay=False, utilidad=CERO)

        res = await self._ledger.registrar_asiento(
            AsientoCrear(
                fecha=_fin_de_mes(anio, mes), origen_tipo="cierre", origen_id=None,
                descripcion=f"Cierre período {anio}-{mes:02d}", idempotency_key=key, lineas=lineas,
            )
        )
        await self._repo.marcar_periodo(periodo, "closed", ahora=ahora)
        log.info("periodo_cerrado", anio=anio, mes=mes, entry_id=res.entry.id, utilidad=str(utilidad))
        return ResultadoCierre(entry=res.entry, replay=res.replay, utilidad=utilidad)
