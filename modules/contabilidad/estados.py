"""Estados financieros derivados del ledger (ADR 0030): balance de comprobación, estado de
resultados, balance general y flujo de efectivo.

Todo se agrega desde `journal_line` (posted) por la clase de la cuenta (primer dígito del código PUC).
Convención de signo por naturaleza: una cuenta `debito` (activo/gasto/costo) tiene saldo `débitos −
créditos`; una `credito` (pasivo/patrimonio/ingreso) tiene `créditos − débitos`. Como cada asiento
cuadra, el balance de comprobación cuadra globalmente y el balance general cierra con la utilidad del
ejercicio como partida de cierre.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.money import cuantizar
from modules.contabilidad.puc_seed import BANCOS, CAJA
from modules.contabilidad.repository import AgregadoCuenta, SqlContabilidadRepository
from modules.contabilidad.schemas import (
    BalanceComprobacion,
    BalanceGeneral,
    EstadoResultados,
    FilaBalanceComprobacion,
    FilaEstado,
    FilaFlujo,
    FlujoEfectivo,
)

CERO = Decimal("0")


def _saldo_natural(a: AgregadoCuenta) -> Decimal:
    return a.debitos - a.creditos if a.naturaleza == "debito" else a.creditos - a.debitos


class EstadosService:
    def __init__(self, repo: SqlContabilidadRepository) -> None:
        self._repo = repo

    async def balance_comprobacion(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> BalanceComprobacion:
        aggs = await self._repo.agregado_por_cuenta(inicio=inicio, fin=fin)
        filas = [
            FilaBalanceComprobacion(
                codigo=a.codigo, nombre=a.nombre, naturaleza=a.naturaleza,
                debitos=a.debitos, creditos=a.creditos, saldo=_saldo_natural(a),
            )
            for a in aggs
        ]
        total_deb = cuantizar(sum((a.debitos for a in aggs), CERO))
        total_cred = cuantizar(sum((a.creditos for a in aggs), CERO))
        return BalanceComprobacion(
            filas=filas, total_debitos=total_deb, total_creditos=total_cred,
            cuadra=total_deb == total_cred,
        )

    async def estado_resultados(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> EstadoResultados:
        aggs = await self._repo.agregado_por_cuenta(inicio=inicio, fin=fin)
        ingresos = [
            FilaEstado(codigo=a.codigo, nombre=a.nombre, valor=cuantizar(a.creditos - a.debitos))
            for a in aggs if a.codigo.startswith("4")
        ]
        costos = [
            FilaEstado(codigo=a.codigo, nombre=a.nombre, valor=cuantizar(a.debitos - a.creditos))
            for a in aggs if a.codigo.startswith("6")
        ]
        gastos = [
            FilaEstado(codigo=a.codigo, nombre=a.nombre, valor=cuantizar(a.debitos - a.creditos))
            for a in aggs if a.codigo.startswith("5")
        ]
        t_ing = cuantizar(sum((f.valor for f in ingresos), CERO))
        t_cos = cuantizar(sum((f.valor for f in costos), CERO))
        t_gas = cuantizar(sum((f.valor for f in gastos), CERO))
        return EstadoResultados(
            ingresos=ingresos, costos=costos, gastos=gastos,
            total_ingresos=t_ing, total_costos=t_cos, total_gastos=t_gas,
            utilidad=cuantizar(t_ing - t_cos - t_gas),
        )

    async def balance_general(self, *, fin: datetime | None = None) -> BalanceGeneral:
        aggs = await self._repo.agregado_por_cuenta(fin=fin)
        activos = [
            FilaEstado(codigo=a.codigo, nombre=a.nombre, valor=cuantizar(a.debitos - a.creditos))
            for a in aggs if a.codigo.startswith("1")
        ]
        pasivos = [
            FilaEstado(codigo=a.codigo, nombre=a.nombre, valor=cuantizar(a.creditos - a.debitos))
            for a in aggs if a.codigo.startswith("2")
        ]
        patrimonio = [
            FilaEstado(codigo=a.codigo, nombre=a.nombre, valor=cuantizar(a.creditos - a.debitos))
            for a in aggs if a.codigo.startswith("3")
        ]
        # Utilidad del ejercicio = ingresos − costos − gastos (partida de cierre del balance).
        util = cuantizar(
            sum((a.creditos - a.debitos for a in aggs if a.codigo.startswith("4")), CERO)
            - sum((a.debitos - a.creditos for a in aggs if a.codigo.startswith("6")), CERO)
            - sum((a.debitos - a.creditos for a in aggs if a.codigo.startswith("5")), CERO)
        )
        t_act = cuantizar(sum((f.valor for f in activos), CERO))
        t_pas = cuantizar(sum((f.valor for f in pasivos), CERO))
        t_pat = cuantizar(sum((f.valor for f in patrimonio), CERO))
        return BalanceGeneral(
            activos=activos, pasivos=pasivos, patrimonio=patrimonio,
            total_activos=t_act, total_pasivos=t_pas, total_patrimonio=t_pat,
            utilidad_ejercicio=util, cuadra=t_act == cuantizar(t_pas + t_pat + util),
        )

    async def flujo_efectivo(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> FlujoEfectivo:
        filas = await self._repo.flujo_efectivo(
            codigos_efectivo=(CAJA, BANCOS), inicio=inicio, fin=fin
        )
        entradas: dict[str, Decimal] = {}
        salidas: dict[str, Decimal] = {}
        for origen, direction, monto in filas:
            destino = entradas if direction == "debit" else salidas
            destino[origen] = destino.get(origen, CERO) + monto
        ent = [FilaFlujo(concepto=k, valor=cuantizar(v)) for k, v in sorted(entradas.items())]
        sal = [FilaFlujo(concepto=k, valor=cuantizar(v)) for k, v in sorted(salidas.items())]
        t_ent = cuantizar(sum((f.valor for f in ent), CERO))
        t_sal = cuantizar(sum((f.valor for f in sal), CERO))
        return FlujoEfectivo(
            entradas=ent, salidas=sal, total_entradas=t_ent, total_salidas=t_sal,
            flujo_neto=cuantizar(t_ent - t_sal),
        )
