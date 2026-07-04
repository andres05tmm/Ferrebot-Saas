"""Acceso a datos del motor contable (ADR 0030). ÚNICO lugar con SQL (regla #2).

Nunca hace commit: solo `flush` (la frontera transaccional es del `get_tenant_db`/sesión del llamador).
Los reportes agregan directo de `journal_line` (la verdad), no del `saldo_cache` (que es una caché
recomputable, mantenida incrementalmente al postear y reconstruible con `recomputar_saldos`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.contabilidad.models import (
    JournalEntry,
    JournalLine,
    PeriodoContable,
    PucCuenta,
    SaldoCache,
)
from modules.contabilidad.puc_seed import parent_de, semilla_puc


@dataclass(frozen=True, slots=True)
class CuentaInfo:
    id: int
    codigo: str
    nombre: str
    naturaleza: str
    imputable: bool


@dataclass(frozen=True, slots=True)
class LineaResuelta:
    """Línea con la cuenta ya resuelta a id/naturaleza, lista para persistir."""

    cuenta: CuentaInfo
    direction: str
    amount: Decimal
    descripcion: str | None
    orden: int


@dataclass(frozen=True, slots=True)
class AgregadoCuenta:
    codigo: str
    nombre: str
    naturaleza: str
    imputable: bool
    debitos: Decimal
    creditos: Decimal


class SqlContabilidadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- PUC ------------------------------------------------------------------
    async def asegurar_puc(self) -> None:
        """Siembra el PUC (idempotente): inserta las cuentas faltantes y cablea `parent_id`.

        Reejecutar no duplica (dedup por `codigo`). Se llama al habilitar el ledger o al proyectar.
        """
        existentes = set(
            (await self._s.execute(select(PucCuenta.codigo))).scalars().all()
        )
        faltantes = [c for c in semilla_puc() if c.codigo not in existentes]
        if not faltantes:
            return
        for c in faltantes:
            self._s.add(
                PucCuenta(
                    codigo=c.codigo, nombre=c.nombre, naturaleza=c.naturaleza,
                    imputable=c.imputable, activo=True,
                )
            )
        await self._s.flush()
        # Cablear parent_id por prefijo (todas las cuentas ya existen con id).
        filas = (await self._s.execute(select(PucCuenta))).scalars().all()
        por_codigo = {f.codigo: f for f in filas}
        codigos = set(por_codigo)
        for f in filas:
            if f.parent_id is None:
                pc = parent_de(f.codigo, codigos)
                if pc is not None:
                    f.parent_id = por_codigo[pc].id
        await self._s.flush()

    async def cuentas_map(self) -> dict[str, CuentaInfo]:
        filas = (await self._s.execute(select(PucCuenta))).scalars().all()
        return {
            f.codigo: CuentaInfo(
                id=f.id, codigo=f.codigo, nombre=f.nombre,
                naturaleza=f.naturaleza, imputable=f.imputable,
            )
            for f in filas
        }

    # --- períodos -------------------------------------------------------------
    async def resolver_periodo(self, fecha: datetime) -> PeriodoContable:
        """Devuelve el período (anio, mes) de la fecha; lo crea `open` si no existe."""
        p = (
            await self._s.execute(
                select(PeriodoContable).where(
                    PeriodoContable.anio == fecha.year, PeriodoContable.mes == fecha.month
                )
            )
        ).scalar_one_or_none()
        if p is None:
            p = PeriodoContable(anio=fecha.year, mes=fecha.month, estado="open")
            self._s.add(p)
            await self._s.flush()
        return p

    async def periodo_de(self, anio: int, mes: int) -> PeriodoContable | None:
        return (
            await self._s.execute(
                select(PeriodoContable).where(
                    PeriodoContable.anio == anio, PeriodoContable.mes == mes
                )
            )
        ).scalar_one_or_none()

    async def marcar_periodo(
        self, periodo: PeriodoContable, estado: str, *, ahora: datetime
    ) -> None:
        """Cambia el candado del período (open|locked|closed). Solo flush."""
        periodo.estado = estado
        periodo.actualizado_en = ahora
        await self._s.flush()

    # --- asientos -------------------------------------------------------------
    async def asiento_por_idempotency(self, key: str) -> JournalEntry | None:
        return (
            await self._s.execute(
                select(JournalEntry).where(JournalEntry.idempotency_key == key)
            )
        ).scalar_one_or_none()

    async def asiento_por_origen(self, origen_tipo: str, origen_id: int) -> JournalEntry | None:
        return (
            await self._s.execute(
                select(JournalEntry)
                .where(
                    JournalEntry.origen_tipo == origen_tipo, JournalEntry.origen_id == origen_id
                )
                .order_by(JournalEntry.id)
                .limit(1)
            )
        ).scalar_one_or_none()

    async def entry_por_id(self, entry_id: int) -> JournalEntry | None:
        return await self._s.get(JournalEntry, entry_id)

    async def insertar_posted(
        self,
        *,
        fecha: datetime,
        periodo_id: int,
        origen_tipo: str,
        origen_id: int | None,
        descripcion: str | None,
        idempotency_key: str | None,
        reverso_de: int | None,
        lineas: list[LineaResuelta],
        ahora: datetime,
    ) -> JournalEntry:
        """Inserta el asiento YA posteado con sus líneas y actualiza el `saldo_cache`. Solo flush."""
        entry = JournalEntry(
            fecha=fecha, periodo_id=periodo_id, estado="posted",
            origen_tipo=origen_tipo, origen_id=origen_id, descripcion=descripcion,
            idempotency_key=idempotency_key, reverso_de=reverso_de, posted_en=ahora,
        )
        self._s.add(entry)
        await self._s.flush()
        for ln in lineas:
            self._s.add(
                JournalLine(
                    entry_id=entry.id, cuenta_id=ln.cuenta.id, direction=ln.direction,
                    amount=ln.amount, descripcion=ln.descripcion, orden=ln.orden,
                )
            )
        await self._s.flush()
        await self._acumular_saldos(periodo_id, lineas, ahora)
        return entry

    async def _acumular_saldos(
        self, periodo_id: int, lineas: list[LineaResuelta], ahora: datetime
    ) -> None:
        """Upsert incremental del saldo por (cuenta, período). El saldo respeta la naturaleza."""
        for ln in lineas:
            fila = (
                await self._s.execute(
                    select(SaldoCache)
                    .where(
                        SaldoCache.cuenta_id == ln.cuenta.id,
                        SaldoCache.periodo_id == periodo_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if fila is None:
                fila = SaldoCache(
                    cuenta_id=ln.cuenta.id, periodo_id=periodo_id,
                    debitos=Decimal("0"), creditos=Decimal("0"), saldo=Decimal("0"),
                )
                self._s.add(fila)
            if ln.direction == "debit":
                fila.debitos = fila.debitos + ln.amount
            else:
                fila.creditos = fila.creditos + ln.amount
            fila.saldo = (
                fila.debitos - fila.creditos
                if ln.cuenta.naturaleza == "debito"
                else fila.creditos - fila.debitos
            )
            fila.actualizado_en = ahora
        await self._s.flush()

    async def recomputar_saldos(self, ahora: datetime) -> None:
        """Reconstruye el `saldo_cache` completo desde `journal_line` (posted). Idempotente."""
        await self._s.execute(SaldoCache.__table__.delete())
        await self._s.flush()
        rows = (
            await self._s.execute(
                select(
                    JournalLine.cuenta_id,
                    JournalEntry.periodo_id,
                    func.coalesce(
                        func.sum(case((JournalLine.direction == "debit", JournalLine.amount), else_=0)), 0
                    ),
                    func.coalesce(
                        func.sum(case((JournalLine.direction == "credit", JournalLine.amount), else_=0)), 0
                    ),
                )
                .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
                .where(JournalEntry.estado == "posted")
                .group_by(JournalLine.cuenta_id, JournalEntry.periodo_id)
            )
        ).all()
        naturaleza = {
            c.id: c.naturaleza for c in (await self._s.execute(select(PucCuenta))).scalars().all()
        }
        for cuenta_id, periodo_id, deb, cred in rows:
            deb, cred = Decimal(deb), Decimal(cred)
            saldo = deb - cred if naturaleza.get(cuenta_id) == "debito" else cred - deb
            self._s.add(
                SaldoCache(
                    cuenta_id=cuenta_id, periodo_id=periodo_id,
                    debitos=deb, creditos=cred, saldo=saldo, actualizado_en=ahora,
                )
            )
        await self._s.flush()

    # --- lecturas para reportes ----------------------------------------------
    async def agregado_por_cuenta(
        self, *, inicio: datetime | None = None, fin: datetime | None = None
    ) -> list[AgregadoCuenta]:
        """Débitos/creditos por cuenta imputable (posted), filtrable por rango de `journal_entry.fecha`."""
        deb = func.coalesce(
            func.sum(case((JournalLine.direction == "debit", JournalLine.amount), else_=0)), 0
        )
        cred = func.coalesce(
            func.sum(case((JournalLine.direction == "credit", JournalLine.amount), else_=0)), 0
        )
        q = (
            select(PucCuenta.codigo, PucCuenta.nombre, PucCuenta.naturaleza, PucCuenta.imputable, deb, cred)
            .join(JournalLine, JournalLine.cuenta_id == PucCuenta.id)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .where(JournalEntry.estado == "posted")
            .group_by(PucCuenta.id, PucCuenta.codigo, PucCuenta.nombre, PucCuenta.naturaleza, PucCuenta.imputable)
            .order_by(PucCuenta.codigo)
        )
        if inicio is not None:
            q = q.where(JournalEntry.fecha >= inicio)
        if fin is not None:
            q = q.where(JournalEntry.fecha <= fin)
        filas = (await self._s.execute(q)).all()
        return [
            AgregadoCuenta(
                codigo=c, nombre=n, naturaleza=nat, imputable=imp,
                debitos=Decimal(d), creditos=Decimal(cr),
            )
            for c, n, nat, imp, d, cr in filas
        ]

    async def agregado_por_cuenta_periodo(self, periodo_id: int) -> list[AgregadoCuenta]:
        """Débitos/creditos por cuenta imputable (posted) acotado a UN período (para el cierre)."""
        deb = func.coalesce(
            func.sum(case((JournalLine.direction == "debit", JournalLine.amount), else_=0)), 0
        )
        cred = func.coalesce(
            func.sum(case((JournalLine.direction == "credit", JournalLine.amount), else_=0)), 0
        )
        q = (
            select(PucCuenta.codigo, PucCuenta.nombre, PucCuenta.naturaleza, PucCuenta.imputable, deb, cred)
            .join(JournalLine, JournalLine.cuenta_id == PucCuenta.id)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .where(JournalEntry.estado == "posted", JournalEntry.periodo_id == periodo_id)
            .group_by(PucCuenta.id, PucCuenta.codigo, PucCuenta.nombre, PucCuenta.naturaleza, PucCuenta.imputable)
            .order_by(PucCuenta.codigo)
        )
        filas = (await self._s.execute(q)).all()
        return [
            AgregadoCuenta(
                codigo=c, nombre=n, naturaleza=nat, imputable=imp,
                debitos=Decimal(d), creditos=Decimal(cr),
            )
            for c, n, nat, imp, d, cr in filas
        ]

    async def flujo_efectivo(
        self, *, codigos_efectivo: tuple[str, ...], inicio: datetime | None = None,
        fin: datetime | None = None,
    ) -> list[tuple[str, str, Decimal]]:
        """(origen_tipo, direction, Σ amount) de las líneas sobre las cuentas de efectivo (posted)."""
        q = (
            select(
                JournalEntry.origen_tipo,
                JournalLine.direction,
                func.coalesce(func.sum(JournalLine.amount), 0),
            )
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(PucCuenta, JournalLine.cuenta_id == PucCuenta.id)
            .where(JournalEntry.estado == "posted", PucCuenta.codigo.in_(codigos_efectivo))
            .group_by(JournalEntry.origen_tipo, JournalLine.direction)
            .order_by(JournalEntry.origen_tipo)
        )
        if inicio is not None:
            q = q.where(JournalEntry.fecha >= inicio)
        if fin is not None:
            q = q.where(JournalEntry.fecha <= fin)
        return [(o, d, Decimal(m)) for o, d, m in (await self._s.execute(q)).all()]

    async def listar_asientos(
        self, *, limit: int = 100, origen_tipo: str | None = None
    ) -> list[JournalEntry]:
        q = select(JournalEntry).order_by(JournalEntry.id.desc()).limit(limit)
        if origen_tipo is not None:
            q = q.where(JournalEntry.origen_tipo == origen_tipo)
        return list((await self._s.execute(q)).scalars().all())

    async def saldos_cache(self) -> list[SaldoCache]:
        return list((await self._s.execute(select(SaldoCache))).scalars().all())
