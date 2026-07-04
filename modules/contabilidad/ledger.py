"""Servicio del libro diario (ADR 0030): valida, postea y reversa asientos.

Invariantes que este servicio hace cumplir (TDD test-primero):
- **débitos = créditos**: un asiento descuadrado JAMÁS se postea (`AsientoDescuadrado`).
- **inmutabilidad**: un asiento `posted` no se edita; la corrección es un espejo (`reversar`).
- **período bloqueado**: postear en un período locked/closed → `PeriodoBloqueado`.
- **idempotencia**: misma `idempotency_key` → replay (devuelve el asiento existente), sin duplicar.

Todo monto pasa por `core.money.cuantizar` (NUMERIC(12,2), ROUND_HALF_UP). No hace commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.config.timezone import now_co
from core.logging import get_logger
from core.money import cuantizar
from modules.contabilidad.errors import (
    AsientoConflicto,
    AsientoDescuadrado,
    AsientoInmutable,
    CuentaInexistente,
    CuentaNoImputable,
    PeriodoBloqueado,
)
from modules.contabilidad.models import JournalEntry
from modules.contabilidad.repository import LineaResuelta, SqlContabilidadRepository
from modules.contabilidad.schemas import AsientoCrear

log = get_logger("contabilidad.ledger")


@dataclass(frozen=True, slots=True)
class ResultadoAsiento:
    entry: JournalEntry
    replay: bool


class LedgerService:
    def __init__(self, repo: SqlContabilidadRepository) -> None:
        self._repo = repo

    async def registrar_asiento(self, data: AsientoCrear) -> ResultadoAsiento:
        """Valida y postea un asiento. Idempotente por `idempotency_key` (replay sin duplicar)."""
        if data.idempotency_key:
            previo = await self._repo.asiento_por_idempotency(data.idempotency_key)
            if previo is not None:
                if not self._payload_compatible(previo, data):
                    raise AsientoConflicto(
                        f"idempotency_key '{data.idempotency_key}' ya existe con otro contenido"
                    )
                return ResultadoAsiento(entry=previo, replay=True)

        lineas = await self._resolver_lineas(data)
        self._validar_cuadre(lineas)

        periodo = await self._repo.resolver_periodo(data.fecha)
        if periodo.estado != "open":
            raise PeriodoBloqueado(
                f"período {periodo.anio}-{periodo.mes:02d} está {periodo.estado}: no acepta asientos"
            )

        ahora = now_co()
        entry = await self._repo.insertar_posted(
            fecha=data.fecha, periodo_id=periodo.id, origen_tipo=data.origen_tipo,
            origen_id=data.origen_id, descripcion=data.descripcion,
            idempotency_key=data.idempotency_key, reverso_de=None, lineas=lineas, ahora=ahora,
        )
        log.info(
            "asiento_posteado", entry_id=entry.id, origen_tipo=data.origen_tipo,
            origen_id=data.origen_id, lineas=len(lineas),
        )
        return ResultadoAsiento(entry=entry, replay=False)

    async def reversar(self, entry_id: int, *, motivo: str | None = None) -> ResultadoAsiento:
        """Corrige un asiento `posted` con su espejo (débitos↔créditos). Idempotente por asiento."""
        original = await self._repo.entry_por_id(entry_id)
        if original is None:
            raise CuentaInexistente(f"asiento {entry_id} inexistente")
        if original.estado != "posted":
            raise AsientoInmutable("solo se reversa un asiento posteado")
        key = f"reverso:{entry_id}"
        previo = await self._repo.asiento_por_idempotency(key)
        if previo is not None:
            return ResultadoAsiento(entry=previo, replay=True)

        cuentas = await self._repo.cuentas_map()
        por_id = {c.id: c for c in cuentas.values()}
        lineas = [
            LineaResuelta(
                cuenta=por_id[ln.cuenta_id],
                direction="credit" if ln.direction == "debit" else "debit",
                amount=ln.amount, descripcion=ln.descripcion, orden=ln.orden,
            )
            for ln in original.lineas
        ]
        periodo = await self._repo.resolver_periodo(original.fecha)
        if periodo.estado != "open":
            raise PeriodoBloqueado(
                f"período {periodo.anio}-{periodo.mes:02d} está {periodo.estado}: no acepta la reversión"
            )
        ahora = now_co()
        entry = await self._repo.insertar_posted(
            fecha=original.fecha, periodo_id=periodo.id, origen_tipo="reverso",
            origen_id=entry_id, descripcion=motivo or f"Reversión del asiento {entry_id}",
            idempotency_key=key, reverso_de=entry_id, lineas=lineas, ahora=ahora,
        )
        log.info("asiento_reversado", original_id=entry_id, reverso_id=entry.id)
        return ResultadoAsiento(entry=entry, replay=False)

    async def anexar_linea(self, entry_id: int, _linea) -> None:
        """Guard de inmutabilidad: un asiento `posted` no admite cambios (usa `reversar`)."""
        entry = await self._repo.entry_por_id(entry_id)
        if entry is None:
            raise CuentaInexistente(f"asiento {entry_id} inexistente")
        if entry.estado == "posted":
            raise AsientoInmutable(
                f"asiento {entry_id} posteado es inmutable: corrige con una reversión"
            )

    # --- helpers --------------------------------------------------------------
    async def _resolver_lineas(self, data: AsientoCrear) -> list[LineaResuelta]:
        cuentas = await self._repo.cuentas_map()
        lineas: list[LineaResuelta] = []
        for i, ln in enumerate(data.lineas):
            cuenta = cuentas.get(ln.cuenta_codigo)
            if cuenta is None:
                raise CuentaInexistente(f"cuenta PUC '{ln.cuenta_codigo}' inexistente")
            if not cuenta.imputable:
                raise CuentaNoImputable(
                    f"cuenta '{ln.cuenta_codigo}' es de agrupación, no recibe movimientos"
                )
            lineas.append(
                LineaResuelta(
                    cuenta=cuenta, direction=ln.direction, amount=cuantizar(ln.amount),
                    descripcion=ln.descripcion, orden=i,
                )
            )
        return lineas

    @staticmethod
    def _validar_cuadre(lineas: list[LineaResuelta]) -> None:
        if not lineas:
            raise AsientoDescuadrado("un asiento sin líneas no cuadra")
        deb = cuantizar(sum((ln.amount for ln in lineas if ln.direction == "debit"), Decimal("0")))
        cred = cuantizar(sum((ln.amount for ln in lineas if ln.direction == "credit"), Decimal("0")))
        if deb != cred:
            raise AsientoDescuadrado(f"débitos ({deb}) ≠ créditos ({cred})")

    @staticmethod
    def _payload_compatible(previo: JournalEntry, data: AsientoCrear) -> bool:
        """El replay exige que el origen coincida (misma key, mismo evento)."""
        return previo.origen_tipo == data.origen_tipo and previo.origen_id == data.origen_id
