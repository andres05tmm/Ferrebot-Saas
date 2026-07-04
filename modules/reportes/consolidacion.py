"""Consolidación del IVA por bimestre (ADR 0027): materializa el Libro IVA y el saldo bimestral.

Hasta hoy el Libro IVA se calculaba AL VUELO en `reportes.repository`. Este servicio deja de depender
de ese cálculo efímero: por período (bimestre) escribe un renglón por documento en `libro_iva` y un
saldo consolidado en `iva_saldos_bimestrales`. Es IDEMPOTENTE — reprocesar el mismo período no duplica:

- `libro_iva`: UPSERT por `referencia` ('venta:{id}' / 'compra_fiscal:{id}') vía el índice único parcial
  de la migración 0034.
- `iva_saldos_bimestrales`: UPSERT por (anio, bimestre) vía la constraint de la 0001.

Solo cruza datos existentes (ventas completadas + compras fiscales); no toca la DIAN. SQL en el
repositorio; la aritmética del período (bimestre → rango de fechas Colombia) es pura.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import rango_dia_co
from modules.reportes.schemas import SaldoBimestral


class BimestreInvalido(ValueError):
    """El bimestre no está en 1..6 (los seis períodos de IVA del año colombiano)."""


def rango_bimestre(anio: int, bimestre: int) -> tuple[date, date]:
    """Primer y último día del bimestre (PURO). Bim 1 = ene-feb … bim 6 = nov-dic."""
    if not 1 <= bimestre <= 6:
        raise BimestreInvalido(bimestre)
    mes_inicio = (bimestre - 1) * 2 + 1
    mes_fin = bimestre * 2
    ultimo_dia = calendar.monthrange(anio, mes_fin)[1]
    return date(anio, mes_inicio, 1), date(anio, mes_fin, ultimo_dia)


@dataclass(frozen=True, slots=True)
class _Agg:
    iva_generado: Decimal
    iva_descontable: Decimal
    renglones: int


class SqlConsolidacionRepository:
    """Único lugar con SQL de la consolidación (regla #2). UPSERTs idempotentes sobre el tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def consolidar(
        self, *, anio: int, bimestre: int, inicio: datetime, fin: datetime
    ) -> _Agg:
        """Materializa libro_iva (renglón por documento) + iva_saldos_bimestrales (saldo). Idempotente."""
        # 1) Renglones 'generado' desde ventas completadas del rango (base=subtotal, iva=impuestos).
        await self._s.execute(
            text(
                "INSERT INTO libro_iva (fecha, tipo, base, iva, referencia) "
                "SELECT date(timezone('America/Bogota', v.fecha)), 'generado', v.subtotal, "
                "       v.impuestos, 'venta:' || v.id "
                "FROM ventas v "
                "WHERE v.estado='completada' AND v.fecha >= :inicio AND v.fecha <= :fin "
                "ON CONFLICT (referencia) WHERE referencia IS NOT NULL DO UPDATE "
                "SET fecha=EXCLUDED.fecha, tipo=EXCLUDED.tipo, base=EXCLUDED.base, iva=EXCLUDED.iva"
            ),
            {"inicio": inicio, "fin": fin},
        )
        # 2) Renglones 'descontable' desde compras fiscales del rango (por creado_en).
        await self._s.execute(
            text(
                "INSERT INTO libro_iva (fecha, tipo, base, iva, referencia) "
                "SELECT date(timezone('America/Bogota', cf.creado_en)), 'descontable', "
                "       coalesce(cf.base,0), coalesce(cf.iva,0), 'compra_fiscal:' || cf.id "
                "FROM compras_fiscal cf "
                "WHERE cf.creado_en >= :inicio AND cf.creado_en <= :fin "
                "ON CONFLICT (referencia) WHERE referencia IS NOT NULL DO UPDATE "
                "SET fecha=EXCLUDED.fecha, tipo=EXCLUDED.tipo, base=EXCLUDED.base, iva=EXCLUDED.iva"
            ),
            {"inicio": inicio, "fin": fin},
        )
        # 3) Saldo del bimestre desde los mismos insumos (no desde libro_iva: mismo cruce, sin doble
        #    conteo si un renglón viejo quedara de otra corrida).
        gen = (
            await self._s.execute(
                text(
                    "SELECT coalesce(sum(impuestos),0) FROM ventas "
                    "WHERE estado='completada' AND fecha >= :inicio AND fecha <= :fin"
                ),
                {"inicio": inicio, "fin": fin},
            )
        ).scalar_one()
        desc = (
            await self._s.execute(
                text(
                    "SELECT coalesce(sum(iva),0) FROM compras_fiscal "
                    "WHERE creado_en >= :inicio AND creado_en <= :fin"
                ),
                {"inicio": inicio, "fin": fin},
            )
        ).scalar_one()
        gen, desc = Decimal(gen), Decimal(desc)
        saldo = gen - desc
        await self._s.execute(
            text(
                "INSERT INTO iva_saldos_bimestrales (anio, bimestre, iva_generado, iva_descontable, saldo) "
                "VALUES (:anio, :bim, :gen, :desc, :saldo) "
                "ON CONFLICT (anio, bimestre) DO UPDATE "
                "SET iva_generado=EXCLUDED.iva_generado, iva_descontable=EXCLUDED.iva_descontable, "
                "    saldo=EXCLUDED.saldo"
            ),
            {"anio": anio, "bim": bimestre, "gen": gen, "desc": desc, "saldo": saldo},
        )
        renglones = (
            await self._s.execute(
                text(
                    "SELECT count(*) FROM libro_iva WHERE referencia IS NOT NULL AND "
                    "fecha >= :d AND fecha <= :h"
                ),
                {"d": inicio.date(), "h": fin.date()},
            )
        ).scalar_one()
        await self._s.commit()
        return _Agg(iva_generado=gen, iva_descontable=desc, renglones=int(renglones))

    async def listar_saldos(self, *, anio: int | None) -> list[SaldoBimestral]:
        """Saldos bimestrales consolidados (todos, o los del año dado), orden anio/bimestre."""
        stmt = (
            "SELECT anio, bimestre, iva_generado, iva_descontable, saldo "
            "FROM iva_saldos_bimestrales "
        )
        params: dict = {}
        if anio is not None:
            stmt += "WHERE anio = :anio "
            params["anio"] = anio
        stmt += "ORDER BY anio, bimestre"
        filas = (await self._s.execute(text(stmt), params)).all()
        return [
            SaldoBimestral(
                anio=f[0], bimestre=f[1],
                iva_generado=Decimal(f[2]) if f[2] is not None else Decimal("0"),
                iva_descontable=Decimal(f[3]) if f[3] is not None else Decimal("0"),
                saldo=Decimal(f[4]) if f[4] is not None else Decimal("0"),
            )
            for f in filas
        ]


class ConsolidacionIVAService:
    """Consolida el IVA de un bimestre (idempotente). Resuelve el rango de fechas (Colombia) y delega."""

    def __init__(self, repo: SqlConsolidacionRepository) -> None:
        self._repo = repo

    async def consolidar_bimestre(self, *, anio: int, bimestre: int) -> SaldoBimestral:
        """Materializa libro_iva + saldo del bimestre. Reprocesar el mismo período no duplica."""
        d, h = rango_bimestre(anio, bimestre)
        inicio, fin = rango_dia_co(d, h)
        agg = await self._repo.consolidar(anio=anio, bimestre=bimestre, inicio=inicio, fin=fin)
        return SaldoBimestral(
            anio=anio, bimestre=bimestre,
            iva_generado=agg.iva_generado, iva_descontable=agg.iva_descontable,
            saldo=agg.iva_generado - agg.iva_descontable,
        )

    async def listar_saldos(self, *, anio: int | None) -> list[SaldoBimestral]:
        return await self._repo.listar_saldos(anio=anio)
